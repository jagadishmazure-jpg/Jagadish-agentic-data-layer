# ADR 0002: Agents read data products through one gateway

**Status:** Accepted

## Context

Agents, MCP clients and A2A callers all need data. Giving each its own database access would spread
access rules across three places and make bronze, silver and personal data reachable by mistake.

## Decision

A single `DataGateway` is the only read path. Its registry contains only gold tables whose contract
sets `agent_exposed: true`. Every call checks identity, grant, purpose (granted, allowed, not
prohibited), columns, typed filters, row scope and row cap, binds parameters, quotes free text and is
written to a hash-chained audit log. MCP and A2A are thin adapters over it.

## Consequences

* One place to test: 14 attacks, PII scan, tamper check.
* Adding a new interface (for example a REST API) cannot weaken the rules.
* The gateway is a throughput bottleneck in principle; at this scale it is not, and in a deployment it
  scales horizontally behind an API gateway.
