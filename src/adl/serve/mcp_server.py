"""MCP server exposing data products (never tables) through the data gateway, bound to one identity.

Five read-only tools. Each call goes through `adl.core.access.DataGateway`, so grants, purposes,
column and row security, typed filters, row caps, untrusted-text quoting and the audit log apply to
MCP clients exactly as to the in-process agents. There is no write tool: actions only happen through
the approval workflow in `adl.domains.retail.agents`.

    adl mcp --identity agent:store-copilot-north    # stdio transport
    adl mcp-demo                                    # scripted in-memory session
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from adl.core.access import DataGateway

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
TOOL_NAMES = ["list_data_products", "describe_data_product", "query_data_product", "get_metric", "search_knowledge"]


def build_server(gateway: DataGateway, identity: str) -> MCPServer:
    gateway._identity(identity)  # fail fast on an unknown identity
    server = MCPServer(f"adl-data-products-{identity.split(':')[-1]}")

    @server.tool(annotations=RO)
    def list_data_products() -> list[dict[str, Any]]:
        """Data products this caller is granted, with owner, classification and allowed purposes."""
        return gateway.list_products(identity)

    @server.tool(annotations=RO)
    def describe_data_product(product: str) -> dict[str, Any]:
        """Columns (minus any denied to this caller), primary key, purposes, freshness and row scope of one product."""
        return gateway.describe(identity, product)

    @server.tool(annotations=RO)
    def query_data_product(
        product: str, purpose: str, columns: list[str] | None = None, filters: list[list[Any]] | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Rows from a data product. filters are [column, op, value] with op in eq, ne, lt, le, gt, ge, in. Free text comes back quoted as untrusted data."""
        return gateway.query(identity, product, purpose, columns, filters, limit)

    @server.tool(annotations=RO)
    def get_metric(metrics: list[str], purpose: str, by: list[str] | None = None, filters: list[list[Any]] | None = None) -> list[dict[str, Any]]:
        """Governed metrics from the semantic layer (domains/<domain>/metrics.yaml), grouped by declared dimensions."""
        return gateway.metric(identity, metrics, purpose, by, filters)

    @server.tool(annotations=RO)
    def search_knowledge(question: str, purpose: str, k: int = 5) -> list[dict[str, Any]]:
        """Hybrid (vector + knowledge graph) search over SOPs, supplier terms and store notes, trimmed to this caller's region."""
        return gateway.search_knowledge(identity, question, purpose, k)

    server.gateway = gateway  # type: ignore[attr-defined]
    return server


async def demo(gateway: DataGateway, identity: str = "agent:store-copilot-north") -> list[str]:
    """Scripted session used by `adl mcp-demo`: what a north-region store copilot would ask, plus three attempts it must not get away with."""
    from mcp import Client

    lines = []
    async with Client(build_server(gateway, identity)) as client:
        tools = await client.list_tools()
        lines.append("tools: " + ", ".join(sorted(t.name for t in tools.tools)))
        lines.append(f"all read-only: {all(t.annotations and t.annotations.read_only_hint for t in tools.tools)}")
        prods = (await client.call_tool("list_data_products", {})).structured_content["result"]
        lines.append(f"products granted: {', '.join(p['name'] for p in prods)}")
        risk = (
            await client.call_tool(
                "query_data_product",
                {
                    "product": "stockout_risk",
                    "purpose": "store_operations",
                    "columns": ["store_id", "sku", "probability"],
                    "filters": [["risk_band", "eq", "high"]],
                    "limit": 200,
                },
            )
        ).structured_content["result"]
        lines.append(f"high stockout risk rows: {len(risk)}; stores seen: {sorted({r['store_id'] for r in risk})}")
        m = (
            await client.call_tool("get_metric", {"metrics": ["revenue_usd", "stockout_rate_pct"], "purpose": "store_operations", "by": ["region"]})
        ).structured_content["result"]
        lines.append(
            "metric by region: " + "; ".join(f"{r['region']} revenue ${r['revenue_usd']:,.0f}, stockout {r['stockout_rate_pct']:.2f}%" for r in m)
        )
        hits = (
            await client.call_tool(
                "search_knowledge", {"question": "What do we do when Copperleaf deliveries are late?", "purpose": "store_operations", "k": 3}
            )
        ).structured_content["result"]
        lines.append(f"knowledge hits: {[h['doc_id'] for h in hits]}")
        for label, args in (
            ("south store", {"product": "sales_daily", "purpose": "store_operations", "filters": [["store_id", "eq", "S05"]]}),
            ("denied column", {"product": "sales_daily", "purpose": "store_operations", "columns": ["cogs_usd"]}),
            ("raw PII table", {"product": "loyalty_pii", "purpose": "store_operations"}),
        ):
            r = await client.call_tool("query_data_product", args)
            lines.append(f"{label}: is_error={r.is_error}")
    _ok, msg = gateway.audit.verify()
    lines.append(f"audit: {msg}; denied reads {len(gateway.audit.events('data.denied'))}")
    return lines
