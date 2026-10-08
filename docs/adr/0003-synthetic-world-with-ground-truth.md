# ADR 0003: A synthetic world with ground truth

**Status:** Accepted

## Context

A public portfolio cannot use real retail data, and value claims need something to be measured
against. Real data would also not reveal true demand on sold-out days or true price elasticities.

## Decision

Build a seeded simulator of a fictional grocer (Wrenfield Grocers). It produces the source feeds with
realistic faults and, separately, the ground truth. Product code (pipeline, models, agents) never
reads ground truth, enforced by a static test; only scoring and the forward value simulation use it.
The model client defaults to an offline mock so everything runs without network access.

## Consequences

* Every number is reproducible and regenerated into the docs; CI fails on drift.
* Value is a simulation result and is labelled as such everywhere.
* The simulator's omissions (no customer loss after a stockout) bias decisions; this is documented in
  ADR 0004 and the value case.
