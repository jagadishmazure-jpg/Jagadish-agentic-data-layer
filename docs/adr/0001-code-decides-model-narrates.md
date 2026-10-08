# ADR 0001: Code decides, the model narrates

**Status:** Accepted

## Context

Agents propose purchase orders and markdowns that cost money. A language model can be persuaded by
text in its context (store notes, documents), can make arithmetic mistakes, and its output varies
between runs. Ordering is arithmetic on a forecast with constraints (lead time, case packs, shelf life).

## Decision

Actions are computed by code (`plan_chain`, the same policy functions the value simulation uses). The
model writes a brief with structured output that must list exactly the planned actions;
`validate_brief` replaces any brief that adds, drops or changes an action with a template. The
executor runs the plan, never the brief.

## Consequences

* The value measured in simulation is the value of the logic that is proposed.
* Prompt injection can at worst mislead the prose of a brief; it cannot add an action
  (`adl injection`: 0 injected actions executed in 4 configurations).
* The model's contribution is limited to explanation; richer reasoning (for example, negotiating a
  supplier split) would need a new decision with its own validation.
