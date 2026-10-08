# Component: price elasticity and markdown

Category price elasticities estimated from the grocer's store price tests, and the smallest markdown
that is expected to clear near-date stock, instead of a flat 30%.

## 1. Purpose

* Measure how demand responds to price without the bias of promotional price changes.
* Mark down only what will not sell at full price, by only as much as needed.
* Send deep markdowns to a person.

## 2. Architecture

```mermaid
flowchart LR
  PT[silver.price_tests: 144 tests] --> DID[difference-in-differences per test]
  DID --> EL[category elasticity: median]
  H[history: markdown days] --> T30[take30: share of demand that buys at 30% off]
  EL --> TK[take at d = take30 x ((1-d)/0.7)^e]
  T30 --> TK
  NEAR[near-date units] --> CH[choose: smallest clearing discount, cap 30%]
  FC[forecast] --> CH
  TK --> CH
  CH --> MC[gold.markdown_candidates]
```

## 3. How it works

1. **Elasticity.** Each test moves one product's shelf price in four stores for a week. Comparing the
   change in test stores with the change in the other four stores (test week versus the two weeks
   before) isolates the price effect. The category elasticity is the median over its tests. The
   forecast's own price coefficient is biased towards zero because price mostly moves with promotions
   that also get a display.
2. **Response.** `take30` is the share of a day's typical demand that bought marked-down units on days
   they did not run out. The response at another discount is extrapolated with the elasticity.
3. **Decision.** No markdown if near-date units can sell at full price; otherwise the smallest
   discount on a 10% grid whose expected take clears the units; the deepest allowed (30%, from
   config) if none does. Discounts deeper than 20% wait for the store manager.

## 4. Key files

| File | Role |
|---|---|
| `src/adl/domains/retail/markdown.py` | Elasticity, response, decision |
| `src/adl/domains/retail/insights.py` | The markdown-candidates product |
| `config/policy.yaml` | Maximum discount and the approval threshold |

## 5. Code excerpts

<!-- code: src/adl/domains/retail/markdown.py::take -->
```python
def take(d: np.ndarray, t30: np.ndarray, e: np.ndarray) -> np.ndarray:
    return t30 * ((1 - d) / (1 - RULE_DISCOUNT)) ** e
```
<!-- /code -->

<!-- code: src/adl/domains/retail/markdown.py::choose -->
```python
def choose(near: np.ndarray, fc: np.ndarray, t30: np.ndarray, e: np.ndarray, max_discount: float = 0.5) -> np.ndarray:
    """Discount per series (0 where no markdown is needed)."""
    grid = GRID[GRID <= max_discount + 1e-9]
    out = np.full(near.shape, grid[-1])
    cleared = np.zeros(near.shape, bool)
    for d in grid[1:]:
        ok = ~cleared & (take(np.full(near.shape, d), t30, e) * fc >= near)
        out[ok] = d
        cleared |= ok
    no_need = near <= take(np.zeros(near.shape), t30, e) * fc
    out[no_need | (near <= 0) | (t30 <= 0)] = 0.0
    return out
```
<!-- /code -->

## 6. Configuration

`markdown.max_discount` 0.3 and `grid_step` 0.1; `approval.markdown_discount` 0.20.

## 7. Commands

```bash
adl elasticity
adl markdown
```

## 8. Real output

<!-- output: elasticity -->
```text
difference-in-differences on 144 store price tests (test stores vs the other stores, 7 days vs the 14 before):
category  estimated  simulator truth  near-date take rate at 30% off
--------  ---------  ---------------  ------------------------------
produce   -1.80      -1.60            0.609
dairy     -1.44      -1.20            0.529
bakery    -1.17      -1.80            0.623
meat      -1.07      -1.40            0.570
frozen    -1.02      -1.30            0.000
pantry    -0.65      -1.10            0.000
```
<!-- /output -->

<!-- output: markdown -->
```text
gold.markdown_candidates at the as-of day (current rule: 30% on everything near date):
category  candidates  near-date units  avg recommended  expected cleared
--------  ----------  ---------------  ---------------  ----------------
bakery    42          447              21.0%            226.6
dairy     1           1                0.0%             1.0
meat      12          109              16.7%            70.1
produce   13          90               20.0%            56.1
```
<!-- /output -->

Frozen and pantry do not expire within the horizon, so they have no near-date take rate. Bakery's
elasticity is under-estimated (-1.17 against -1.80); the effect is limited because the take rate at
30% off is measured directly from history and only the extrapolation to other discounts uses the
elasticity.

## 9. Tests and gates

`tests/test_models.py`: elasticity has the right sign and is in a plausible range; markdowns never
exceed the cap; candidates respect the policy. The value of markdowns alone is a separate ledger lever
(VL-RET-009 to 012).

## 10. Guardrails

* Cap at 30%; above 20% needs a person; every markdown is a dry run.
* The model writing the brief cannot change a discount; the validator rejects any difference.

## 11. Security and governance

Markdown candidates are a data product with acceptable use limited to markdown optimisation and store
operations; pricing by individual customer is prohibited on customer data.

## 12. Observability

Estimated versus realised take at each discount; the share of markdowns rejected by managers.

## 13. Failure modes

| Failure | Effect | Handling |
|---|---|---|
| Elasticity biased | Discount too shallow or deep | Price tests instead of promo variation; cap and approval |
| Markdown cannibalises full-price sales | Margin lost | The simulator includes cannibalisation; value is net |
| Shallower markdown leaves more waste | Waste rises | Reported honestly: markdown-only lever raises waste $628 |

## 14. Mapping to cloud services

| Here | Azure | Google Cloud | AWS |
|---|---|---|---|
| Elasticity job | Microsoft Fabric notebook or Azure Machine Learning job | BigQuery SQL or Vertex AI | SageMaker processing over S3 |
| Candidates product | OneLake Delta table behind the gateway; identities in Entra ID | BigQuery table | Glue table, Athena |

## 15. Limitations

* One elasticity per category; products within a category differ.
* The response curve is extrapolated from a single observed discount.

## 16. Interview talking points

* "Promotional price changes come with a display, so they overstate price response; the price tests
  are the clean experiment."
* "The markdown lever alone raises waste a little and still adds $1,913, because the margin kept is
  bigger than the extra waste. I report both."
