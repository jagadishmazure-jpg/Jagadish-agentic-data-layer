"""A domain-neutral approval workflow on Microsoft Agent Framework, used by every domain after retail.

    plan ──> narrate ──> policy ──┬── (anything above a threshold) ──> approval gate (request_info) ──┐
                                  └── (all below thresholds) ─────────────────────────────────────────┴─> execute (dry run)

It is the retail workflow (adl.domains.retail.agents) with the domain parts passed in as a `Spec`:

* plan: actions are computed by domain code before the workflow runs (`Deps.plans`, one list per
  group: a loan officer, a claims queue, a ward). The plan node only attaches them and reads the
  group's free-text notes through the gateway, which returns them quoted as untrusted data.
* narrate: an Agent writes the brief with structured output. `validate_brief` rejects a brief whose
  actions differ from the plan and a template brief is used instead. The model never decides.
* policy: the domain marked each action `needs_approval` from its thresholds.
* approval gate: pauses with `ctx.request_info`; the decision must come from a person (`person:`
  prefix) and name the digest of exactly the actions it approves; it can reject lines.
* execute: a dry-run executor that writes what it would do to the hash-chained audit log, linked to
  the value-ledger row of the action's lever. No live executor exists.

`MockNarrator` is the deterministic offline model. With `gullible=True` it obeys instructions it can
see outside <untrusted_data> tags, using the domain's injection parsers, which is how the injection
tests show which defence stops what.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, BaseChatClient, ChatResponse, Executor, Message, WorkflowBuilder, WorkflowContext, handler, response_handler
from agent_framework._tools import FunctionInvocationLayer
from pydantic import BaseModel, Field

from adl.core import guardrails
from adl.core.access import DataGateway
from adl.core.audit import AuditLog
from adl.core.llm import estimate_tokens, get_chat_client


class BriefAction(BaseModel):
    id: str
    kind: str
    subject: str
    quantity: float


class Brief(BaseModel):
    group: str
    headline: str
    summary: str
    actions: list[BriefAction] = Field(default_factory=list)
    flagged_notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    group: str
    subject: str
    quantity: float
    value_usd: float
    reason: str
    needs_approval: bool
    approver_role: str


@dataclass
class ApprovalRequest:
    group: str
    digest: str
    actions: tuple[tuple[str, str, str, float, float], ...]  # id, kind, subject, quantity, value
    brief_headline: str


@dataclass
class ApprovalDecision:
    approver: str
    digest: str
    approved: tuple[str, ...]
    rejected: tuple[str, ...] = ()
    note: str = ""


Injection = Callable[[re.Match, list[BriefAction]], list[BriefAction]]


@dataclass(frozen=True)
class Spec:
    workflow: str  # workflow name
    narrator: str  # agent name
    instructions: str
    planner: str  # identity that reads notes and context
    purpose: str
    notes_product: str
    group_column: str  # column of the notes product that holds the group
    note_id_pattern: str  # e.g. r"PN-\d{3}"
    ledger: dict[str, str]  # action kind -> value-ledger id
    kinds: tuple[str, ...]
    injections: tuple[tuple[str, Injection], ...] = ()
    context: Callable[[DataGateway, str], list[str]] | None = None  # extra facts for the brief (e.g. top risks)


@dataclass
class Case:
    group: str
    actions: list[Action] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    brief: Brief | None = None
    brief_issues: list[str] = field(default_factory=list)
    used_fallback: bool = False
    injection_obeyed: bool = False
    prompt_tokens: int = 0
    output_tokens: int = 0
    decision: ApprovalDecision | None = None
    approval_problems: list[str] = field(default_factory=list)
    executed: list[Action] = field(default_factory=list)
    trail: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[Action]:
        return [a for a in self.actions if a.needs_approval]


def digest(actions: list[Action]) -> str:
    body = json.dumps([[a.id, a.kind, a.group, a.subject, a.quantity] for a in actions], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


FACTS_RE = re.compile(r"FACTS:\s*(\{.*?\})\s*\nNOTES:", re.S)


class MockNarrator(FunctionInvocationLayer, BaseChatClient):
    """Deterministic offline model; see the module docstring."""

    OTEL_PROVIDER_NAME = "adl-mock"

    def __init__(self, spec: Spec, gullible: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spec = spec
        self.gullible = gullible

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        user = next((m.text for m in reversed(list(messages)) if m.role == "user"), "")
        m = FACTS_RE.search(user)
        facts = json.loads(m.group(1)) if m else {"group": "?", "actions": []}
        actions = [BriefAction(**a) for a in facts["actions"]]
        pat = rf"\[({self.spec.note_id_pattern})\]\s*<untrusted_data>(.*?)</untrusted_data>"
        flagged = [nid for nid, body in re.findall(pat, user, re.S) if guardrails.screen(body).flagged]
        visible = re.sub(r"<untrusted_data>.*?</untrusted_data>", "", user, flags=re.S)
        if self.gullible:
            for regex, apply in self.spec.injections:
                for mm in re.finditer(regex, visible, re.I):
                    actions = apply(mm, actions)
        kinds: dict[str, int] = {}
        for a in facts["actions"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        headline = f"{facts['group']}: " + (", ".join(f"{v} {k.replace('_', ' ')}s" for k, v in sorted(kinds.items())) or "no actions") + " proposed"
        summary = f"{len(facts.get('needs_approval', []))} need your approval. Watch: {', '.join(facts.get('context', [])) or 'nothing flagged'}."
        brief = Brief(group=facts["group"], headline=headline, summary=summary, actions=actions, flagged_notes=flagged)
        return ChatResponse(messages=[Message(role="assistant", contents=[brief.model_dump_json()])], model="adl-mock")


def build_prompt(case: Case, context: list[str], guard: bool = True) -> str:
    facts = {
        "group": case.group,
        "actions": [{"id": a.id, "kind": a.kind, "subject": a.subject, "quantity": a.quantity} for a in case.actions],
        "needs_approval": [a.id for a in case.pending],
        "context": context,
    }
    lines = ["Write the brief.", "FACTS: " + json.dumps(facts, sort_keys=True), "NOTES:"]
    for n in case.notes:
        raw = n["text_redacted"]
        if guard:
            lines.append(f"[{n['note_id']}] {raw}")  # the gateway already returned it quoted
        else:
            lines.append(f"[{n['note_id']}] " + re.sub(r"</?untrusted_data>", "", raw).replace("&lt;", "<").replace("&gt;", ">"))
    return "\n".join(lines)


def validate_brief(brief: Brief, case: Case) -> list[str]:
    issues = []
    planned = {a.id: a for a in case.actions}
    seen = set()
    for a in brief.actions:
        p = planned.get(a.id)
        if p is None:
            issues.append(f"adds action {a.kind} {a.subject} x{a.quantity:g} that code did not plan")
        elif (a.kind, a.subject, a.quantity) != (p.kind, p.subject, p.quantity):
            issues.append(f"changes {a.id} to {a.kind} {a.subject} x{a.quantity:g}")
        seen.add(a.id)
    missing = set(planned) - seen
    if missing:
        issues.append(f"drops {len(missing)} planned action(s)")
    if brief.group != case.group:
        issues.append("wrong group")
    return issues


def fallback_brief(case: Case) -> Brief:
    return Brief(
        group=case.group,
        headline=f"{case.group}: template brief ({len(case.actions)} actions)",
        summary="The model brief failed validation, so this template lists the planned actions exactly.",
        actions=[BriefAction(id=a.id, kind=a.kind, subject=a.subject, quantity=a.quantity) for a in case.actions],
    )


@dataclass
class Deps:
    gateway: DataGateway
    audit: AuditLog
    plans: dict[str, list[Action]]
    spec: Spec
    guard: bool = True
    validate: bool = True
    gullible: bool = False
    cases: dict[str, Case] = field(default_factory=dict)


class Node(Executor):
    node = "node"

    def __init__(self, deps: Deps) -> None:
        super().__init__(id=self.node)
        self.deps = deps


class PlanNode(Node):
    node = "plan"

    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Case]) -> None:
        d = self.deps
        case.trail.append(self.node)
        case.actions = list(d.plans.get(case.group, []))
        case.notes = d.gateway.query(
            d.spec.planner, d.spec.notes_product, d.spec.purpose, filters=[[d.spec.group_column, "eq", case.group]], limit=50
        )
        d.audit.append(d.spec.planner, "plan.proposed", {"group": case.group, "actions": len(case.actions), "digest": digest(case.actions)})
        await ctx.send_message(case)


class NarrateNode(Node):
    node = "narrate"

    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Case]) -> None:
        d = self.deps
        case.trail.append(self.node)
        context = d.spec.context(d.gateway, case.group) if d.spec.context else []
        prompt = build_prompt(case, context, d.guard)
        agent = Agent(
            client=get_chat_client(lambda: MockNarrator(d.spec, gullible=d.gullible)), instructions=d.spec.instructions, name=d.spec.narrator
        )
        resp = await agent.run(prompt, options={"response_format": Brief})
        brief = resp.value if isinstance(resp.value, Brief) else Brief.model_validate_json(resp.text)
        case.injection_obeyed = any(a.id == "INJECTED" for a in brief.actions) or bool(validate_brief(brief, case))
        case.prompt_tokens = estimate_tokens(d.spec.instructions + prompt)
        case.output_tokens = estimate_tokens(brief.model_dump_json())
        case.brief_issues = validate_brief(brief, case) if d.validate else []
        case.used_fallback = bool(case.brief_issues)
        case.brief = fallback_brief(case) if case.used_fallback else brief
        d.audit.append("agent:narrator", "brief.written", {"group": case.group, "fallback": case.used_fallback, "issues": case.brief_issues})
        await ctx.send_message(case)


class PolicyNode(Node):
    node = "policy"

    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Case]) -> None:
        case.trail.append(self.node)
        self.deps.audit.append(
            "policy", "policy.checked", {"group": case.group, "auto": len(case.actions) - len(case.pending), "needs_approval": len(case.pending)}
        )
        await ctx.send_message(case)


class ApprovalGate(Node):
    node = "approval_gate"

    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Case, Case]) -> None:
        case.trail.append(self.node)
        req = ApprovalRequest(
            case.group,
            digest(case.pending),
            tuple((a.id, a.kind, a.subject, a.quantity, a.value_usd) for a in case.pending),
            case.brief.headline if case.brief else "",
        )
        self.deps.audit.append("policy", "approval.requested", {"group": case.group, "digest": req.digest, "actions": len(req.actions)})
        await ctx.request_info(req, ApprovalDecision)

    @response_handler
    async def decided(self, req: ApprovalRequest, decision: ApprovalDecision, ctx: WorkflowContext[Case, Case]) -> None:
        case = self.deps.cases[req.group]
        case.decision = decision
        problems = []
        if not decision.approver.startswith("person:"):
            problems.append(f"approver {decision.approver} is not a person")
        if decision.digest != req.digest:
            problems.append("approval digest does not match the proposed actions")
        unknown = set(decision.approved) - {a[0] for a in req.actions}
        if unknown:
            problems.append(f"approves actions that were not requested: {sorted(unknown)}")
        case.approval_problems = problems
        self.deps.audit.append(
            decision.approver,
            "approval.decided",
            {
                "group": req.group,
                "digest": decision.digest,
                "approved": list(decision.approved),
                "rejected": list(decision.rejected),
                "problems": problems,
            },
        )
        await ctx.send_message(case)


class ExecuteNode(Node):
    node = "execute"

    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Case, Case]) -> None:
        case.trail.append(self.node)
        approved = set()
        if case.decision and not case.approval_problems:
            approved = set(case.decision.approved) - set(case.decision.rejected)
        for a in case.actions:
            if a.needs_approval and a.id not in approved:
                continue
            case.executed.append(a)
            self.deps.audit.append(
                "executor:dry-run",
                "action.dry_run",
                {
                    "id": a.id,
                    "kind": a.kind,
                    "group": a.group,
                    "subject": a.subject,
                    "quantity": a.quantity,
                    "value_usd": a.value_usd,
                    "ledger": self.deps.spec.ledger[a.kind],
                },
            )
        await ctx.yield_output(case)


def build(deps: Deps):
    plan, narrate, pol, gate, ex = PlanNode(deps), NarrateNode(deps), PolicyNode(deps), ApprovalGate(deps), ExecuteNode(deps)
    return (
        WorkflowBuilder(start_executor=plan, name=deps.spec.workflow, max_iterations=20)
        .add_edge(plan, narrate)
        .add_edge(narrate, pol)
        .add_edge(pol, gate, condition=lambda c: bool(c.pending))
        .add_edge(pol, ex, condition=lambda c: not c.pending)
        .add_edge(gate, ex)
        .build()
    )


async def run_group(deps: Deps, group: str, answer: Callable[[ApprovalRequest], ApprovalDecision]) -> Case:
    case = Case(group)
    deps.cases[group] = case
    wf = build(deps)
    res = await wf.run(case)
    pending = res.get_request_info_events()
    while pending:
        ev = pending[0]
        res = await wf.run(responses={ev.request_id: answer(ev.data)})
        pending = res.get_request_info_events()
    outs = res.get_outputs()
    return outs[-1] if outs else case


async def run_all(deps: Deps, groups: list[str], answer: Callable[[ApprovalRequest], ApprovalDecision]) -> list[Case]:
    return [await run_group(deps, g, answer) for g in groups]


def summarize(cases: list[Case], kinds: tuple[str, ...]) -> dict[str, Any]:
    acts = [a for c in cases for a in c.actions]
    return {
        "groups": len(cases),
        "actions": len(acts),
        "by_kind": {k: sum(1 for a in acts if a.kind == k) for k in kinds},
        "auto_below_threshold": sum(1 for a in acts if not a.needs_approval),
        "sent_for_approval": sum(1 for a in acts if a.needs_approval),
        "rejected_by_person": sum(len(c.decision.rejected) for c in cases if c.decision),
        "executed_dry_run": sum(len(c.executed) for c in cases),
        "fallback_briefs": sum(c.used_fallback for c in cases),
    }


def injection_matrix(make_deps: Callable[[bool, bool], Deps], groups: list[str], answer) -> list[dict]:
    """Gullible model on the given groups, every combination of quoting and the validator."""
    import asyncio

    rows = []
    for guard in (True, False):
        for validate in (True, False):
            deps = make_deps(guard, validate)
            cases = asyncio.run(run_all(deps, groups, answer))
            rows.append(
                {
                    "quoting": "on" if guard else "off",
                    "validator": "on" if validate else "off",
                    "model_obeyed": sum(c.injection_obeyed for c in cases),
                    "shown_to_approver": sum(any(a.id == "INJECTED" for a in c.brief.actions) for c in cases),
                    "fallback_briefs": sum(c.used_fallback for c in cases),
                    "injected_actions_executed": sum(1 for c in cases for a in c.executed if a.id == "INJECTED"),
                    "executed_actions_match_plan": all(
                        [a.id for a in c.executed]
                        == [a.id for a in c.actions if not a.needs_approval or a.id in set(c.decision.approved if c.decision else ())]
                        for c in cases
                    ),
                }
            )
    return rows
