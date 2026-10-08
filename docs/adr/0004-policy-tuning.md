# ADR 0004: Policy tuning on separate seeds, and choosing lost sales over a little net value

**Status:** Accepted

## Context

Two policy settings matter most: the perishable safety-stock level (`service_z.perishable`) and the
maximum markdown (`markdown.max_discount`). Choosing them on the same simulated futures that are
reported would overstate value.

## Decision

`adl tune` evaluates a 3 x 3 grid on ten tuning seeds that are never reported. The reported results
use 30 different seeds.

On the tuning seeds the highest net value is z = -0.25 with a 30% cap: $10,397 per 28 days, but it
recovers only $2,457 of lost sales. The chosen setting, z = 0 with a 30% cap, earns $9,565 (about $832
less) and recovers $6,971 of lost sales (about $4,514 more). Deeper caps (50%) lose margin at every
service level.

The simulator does not model customers who leave or shop elsewhere after finding an empty shelf, so
it undervalues lost sales. Trading a small amount of simulated net value for a large reduction in
lost sales is the safer business choice until that effect is modelled.

## Consequences

* Reported net value is lower than the best tuning result, deliberately.
* The stockout-rate target is still missed; raising z further (z = 0.5) cuts lost sales more
  ($12,093) but costs over $4,000 of net value on the tuning seeds.
* Adding a customer-loss term to the simulator is the next modelling step; the grid should be rerun
  when it exists.
