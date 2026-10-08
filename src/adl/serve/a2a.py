"""A2A (Agent2Agent) endpoint so other agents can ask this data layer for data products.

Implements the parts of the A2A protocol a data provider needs: an agent card describing the skills
and the auth scheme, and JSON-RPC 2.0 `message/send` whose message carries one `data` part
`{"skill": ..., "args": {...}}`. The bearer token is mapped to a gateway identity by an injected
verifier; in Azure that is Microsoft Entra ID token validation (the token's app id -> agent identity),
here a fixed demo mapping. Every request runs through the same `DataGateway` as MCP and the agents.

`app()` wraps it in a Starlette ASGI app (Starlette ships with the MCP SDK). Nothing is hosted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from adl.core.access import AccessDenied, DataGateway

PROTOCOL_VERSION = "0.3.0"
INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32600, -32601, -32602
UNAUTHENTICATED, ACCESS_DENIED = -32001, -32003
SKILLS = {
    "query_data_product": "Rows from a granted data product for a stated purpose (typed filters, row cap, row and column security).",
    "get_metric": "Governed metrics from the semantic layer, grouped by declared dimensions.",
    "search_knowledge": "Hybrid vector and knowledge-graph search over SOPs, supplier terms and store notes.",
    "list_data_products": "The data products the caller is granted.",
}
DEMO_TOKENS = {"demo-north": "agent:store-copilot-north", "demo-insights": "agent:insights"}


def agent_card(base_url: str = "http://localhost:8080") -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "Wrenfield Grocers data layer",
        "description": "Governed data products, metrics and knowledge for agents. Read-only; actions go through a human-approved workflow.",
        "url": f"{base_url}/a2a",
        "version": "0.1.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "securitySchemes": {
            "entra": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Microsoft Entra ID access token for an agent identity",
            }
        },
        "security": [{"entra": []}],
        "skills": [{"id": k, "name": k.replace("_", " "), "description": v, "tags": ["data", "read-only"]} for k, v in SKILLS.items()],
    }


def _error(rid, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


class A2AEndpoint:
    def __init__(self, gateway: DataGateway, verify: Callable[[str], str | None] | None = None) -> None:
        self.gateway = gateway
        self.verify = verify or DEMO_TOKENS.get

    def handle(self, request: Any, token: str | None) -> dict[str, Any]:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        rid = request.get("id")
        if request["method"] != "message/send":
            return _error(rid, METHOD_NOT_FOUND, f"method {request['method']!r} is not supported")
        identity = self.verify(token or "")
        if not identity:
            return _error(rid, UNAUTHENTICATED, "missing or invalid bearer token")
        try:
            parts = request["params"]["message"]["parts"]
            data = next(p["data"] for p in parts if p.get("kind") == "data")
            skill, args = data["skill"], dict(data.get("args") or {})
        except (KeyError, TypeError, StopIteration):
            return _error(rid, INVALID_PARAMS, "message needs one data part {skill, args}")
        if skill not in SKILLS:
            return _error(rid, INVALID_PARAMS, f"unknown skill {skill!r}")
        gw = self.gateway
        try:
            if skill == "list_data_products":
                result: Any = gw.list_products(identity)
            elif skill == "query_data_product":
                result = gw.query(identity, args["product"], args["purpose"], args.get("columns"), args.get("filters"), args.get("limit", 50))
            elif skill == "get_metric":
                result = gw.metric(identity, args["metrics"], args["purpose"], args.get("by"), args.get("filters"))
            else:
                result = gw.search_knowledge(identity, args["question"], args["purpose"], args.get("k", 5))
        except AccessDenied as e:
            return _error(rid, ACCESS_DENIED, f"{e.code}: {e.detail}")
        except (KeyError, TypeError) as e:
            return _error(rid, INVALID_PARAMS, f"missing argument {e}")
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"adl:{rid}:{skill}")),
                "parts": [{"kind": "data", "data": {"skill": skill, "result": result}}],
            },
        }


def app(endpoint: A2AEndpoint):  # pragma: no cover - exercised in tests through Starlette's TestClient
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def card(_: Request) -> JSONResponse:
        return JSONResponse(agent_card())

    async def rpc(req: Request) -> JSONResponse:
        auth = req.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        try:
            body = await req.json()
        except ValueError:
            return JSONResponse(_error(None, -32700, "parse error"))
        return JSONResponse(endpoint.handle(body, token))

    return Starlette(routes=[Route("/.well-known/agent-card.json", card), Route("/a2a", rpc, methods=["POST"])])


def send(skill: str, args: dict, rid: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "message/send",
        "params": {"message": {"role": "user", "messageId": f"m{rid}", "parts": [{"kind": "data", "data": {"skill": skill, "args": args}}]}},
    }


def demo(gateway: DataGateway) -> list[str]:
    ep = A2AEndpoint(gateway)
    lines = [f"agent card: {agent_card()['name']}, skills {', '.join(SKILLS)}"]
    r = ep.handle(send("get_metric", {"metrics": ["revenue_usd"], "purpose": "performance_reporting", "by": ["region"]}), "demo-insights")
    lines.append("insights metric: " + "; ".join(f"{x['region']} ${x['revenue_usd']:,.0f}" for x in r["result"]["parts"][0]["data"]["result"]))
    r = ep.handle(send("query_data_product", {"product": "customer_segments", "purpose": "performance_reporting", "limit": 5}), "demo-insights")
    lines.append(f"customer segments rows: {len(r['result']['parts'][0]['data']['result'])} (aggregates only, groups under 10 suppressed)")
    for label, req, tok in (
        ("no token", send("list_data_products", {}), None),
        (
            "targeting purpose",
            send("query_data_product", {"product": "customer_segments", "purpose": "individual_customer_profiling"}),
            "demo-insights",
        ),
        ("unknown method", {"jsonrpc": "2.0", "id": 9, "method": "tasks/cancel"}, "demo-north"),
    ):
        lines.append(f"{label}: error {ep.handle(req, tok)['error']['code']}")
    return lines
