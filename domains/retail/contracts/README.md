# domains/retail/contracts

Retail data contracts, validated in CI by `adl.core.contracts` and enforced on every build. Each line shows the contract's own description and its classification.

| File | What it does |
|---|---|
| `gold.customer_segments.yaml` | Aggregated loyalty segments per store (internal, agent-exposed) |
| `gold.demand_forecast.yaml` | Daily demand forecast per store and product for the next 14 days, with an 80% upper bound, from the ridge model in adl.domains.retail.forecast (internal, agent-exposed) |
| `gold.inventory_position.yaml` | Stock position per store and product at the as-of day: on hand, on order, near-expiry units and days of cover (internal, agent-exposed) |
| `gold.markdown_candidates.yaml` | Near-date stock per store and product with the recommended markdown and the expected units cleared (internal, agent-exposed) |
| `gold.promo_plan.yaml` | Promotions planned or running, per product, with the promoted price ratio (internal, agent-exposed) |
| `gold.sales_daily.yaml` | Sales, stock, waste and estimated lost sales per store, product and day (internal, agent-exposed) |
| `gold.stockout_risk.yaml` | Probability that each store-product sells out within the next three days given stock, inbound orders and the forecast (internal, agent-exposed) |
| `gold.store_notes.yaml` | Store-manager notes for agents: personal data redacted, prompt-injection attempts flagged; agents receive the text quoted as untrusted data (internal, agent-exposed) |
| `gold.supplier_performance.yaml` | Realised supplier lead time (mean and 90th percentile), on-time rate and fill rate from receipts (internal, agent-exposed) |
| `gold.value_ledger.yaml` | Business value by lever from the seeded forward simulation versus the current rules, with 95% intervals and the estimated AI and compute cost (internal, agent-exposed) |
| `silver.inventory.yaml` | End-of-day stock per store and product, with units thrown away (expired) and units on their last sellable day tomorrow (internal) |
| `silver.loyalty_customers.yaml` | Pseudonymised loyalty members: a keyed hash replaces the customer id, the postcode is cut to its district and no name, e-mail or phone is kept (confidential) |
| `silver.loyalty_pii.yaml` | Direct identifiers of loyalty members, split from everything else (restricted) |
| `silver.loyalty_visits.yaml` | Loyalty visits by pseudonymised customer: store, day and basket value (confidential) |
| `silver.pos_sales.yaml` | Point-of-sale sales per store, product and day after de-duplication of resent batches; rows failing the checks are quarantined (internal) |
| `silver.price_tests.yaml` | Store price tests from the test-and-learn programme: a product's shelf price moved in a few stores for one week (internal) |
| `silver.prices.yaml` | Regular shelf price per product and day (list price with list-price changes applied), including planned future days (internal) |
| `silver.products.yaml` | Product master: one row per product with category, list price, unit cost, supplier, case pack and shelf life (internal) |
| `silver.promotions.yaml` | Planned chain-wide promotions: product, first and last day, price ratio and whether the product gets a display (internal) |
| `silver.purchase_orders.yaml` | Purchase orders with the receipt: realised lead time, on-time flag and fill rate per order (internal) |
| `silver.store_events.yaml` | Local events near a store (festivals, fairs) that lift footfall, known ahead of time (internal) |
| `silver.store_notes.yaml` | Store-manager notes with personal data redacted and a prompt-injection screen result per note (internal) |
| `silver.stores.yaml` | Store master: store id, name, region and relative size (internal) |
| `silver.suppliers.yaml` | Supplier master: id, name, categories supplied and the contractual (nominal) lead time (internal) |
| `silver.weather.yaml` | Daily temperature anomaly and rain flag per region (history plus the forecast window) (public) |
