# ADR 0005: Approvals bound to a digest; execution is a dry run

**Status:** Accepted

## Context

Approvals in chat tools are easy to forge or replay: an "approved" message can be attached to a
different set of actions, or come from an automated account. And there is no real ERP or pricing
system to call.

## Decision

The approval gate sends the SHA-256 digest of exactly the pending actions. A decision is accepted only
if the approver id starts with `person:`, the digest matches, and every approved id was requested;
otherwise nothing pending executes. Thresholds come from `config/policy.yaml`. The executor writes
`action.dry_run` audit records with the value-ledger id and calls nothing.

## Consequences

* A stale approval approves nothing; an agent cannot approve; extra ids are refused (tests cover all
  three).
* The workflow is safe to demonstrate anywhere.
* A live executor needs its own ADR: idempotency keys, rollback for markdowns, and reconciliation of
  executed orders against the ledger.
