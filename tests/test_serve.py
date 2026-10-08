"""MCP server and A2A endpoint: data products only, read-only, same gateway rules."""

import asyncio

import pytest
from mcp import Client
from starlette.testclient import TestClient

from adl.core.access import AccessDenied
from adl.serve import a2a
from adl.serve.mcp_server import TOOL_NAMES, build_server, demo


def _call(gw, identity, name, args):
    async def go():
        async with Client(build_server(gw, identity)) as c:
            return await c.call_tool(name, args)

    return asyncio.run(go())


def test_mcp_tools_are_exactly_the_read_only_five(gw):
    async def go():
        async with Client(build_server(gw, "agent:replenishment")) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(go())
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)


def test_mcp_query_returns_rows(gw):
    r = _call(gw, "agent:replenishment", "query_data_product", {"product": "supplier_performance", "purpose": "replenishment"})
    assert not r.is_error and len(r.structured_content["result"]) == 6


@pytest.mark.parametrize(
    "args",
    [
        {"product": "loyalty_pii", "purpose": "store_operations"},
        {"product": "sales_daily", "purpose": "store_operations", "columns": ["cogs_usd"]},
        {"product": "sales_daily", "purpose": "store_operations", "filters": [["store_id", "eq", "S05"]]},
        {"product": "sales_daily", "purpose": "individual_customer_profiling"},
    ],
    ids=["pii table", "denied column", "other region", "prohibited purpose"],
)
def test_mcp_denials_are_errors(gw, args):
    assert _call(gw, "agent:store-copilot-north", "query_data_product", args).is_error


def test_mcp_server_refuses_an_unknown_identity(gw):
    with pytest.raises(AccessDenied):
        build_server(gw, "agent:nobody")


def test_mcp_demo_runs(gw):
    lines = asyncio.run(demo(gw))
    assert "all read-only: True" in lines and lines[-1].startswith("audit:")


def test_agent_card_describes_skills_and_auth():
    card = a2a.agent_card()
    assert {s["id"] for s in card["skills"]} == set(a2a.SKILLS)
    assert card["securitySchemes"]["entra"]["scheme"] == "bearer"


def test_a2a_message_send_returns_data(gw):
    ep = a2a.A2AEndpoint(gw)
    r = ep.handle(a2a.send("list_data_products", {}), "demo-insights")
    assert r["result"]["parts"][0]["data"]["result"]


@pytest.mark.parametrize(
    "req,token,code",
    [
        ("not json-rpc", "demo-north", a2a.INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1, "method": "tasks/get"}, "demo-north", a2a.METHOD_NOT_FOUND),
        (a2a.send("list_data_products", {}), None, a2a.UNAUTHENTICATED),
        (a2a.send("list_data_products", {}), "forged", a2a.UNAUTHENTICATED),
        (a2a.send("drop_tables", {}), "demo-north", a2a.INVALID_PARAMS),
        ({"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"parts": []}}}, "demo-north", a2a.INVALID_PARAMS),
        (a2a.send("query_data_product", {"product": "loyalty_pii", "purpose": "store_operations"}), "demo-north", a2a.ACCESS_DENIED),
        (a2a.send("query_data_product", {"purpose": "store_operations"}), "demo-north", a2a.INVALID_PARAMS),
    ],
    ids=["not a request", "unknown method", "no token", "bad token", "unknown skill", "no data part", "access denied", "missing argument"],
)
def test_a2a_errors(gw, req, token, code):
    assert a2a.A2AEndpoint(gw).handle(req, token)["error"]["code"] == code


def test_a2a_over_http(gw):
    client = TestClient(a2a.app(a2a.A2AEndpoint(gw)))
    assert client.get("/.well-known/agent-card.json").json()["name"] == "Wrenfield Grocers data layer"
    ok = client.post(
        "/a2a", json=a2a.send("get_metric", {"metrics": ["units"], "purpose": "store_operations"}), headers={"Authorization": "Bearer demo-north"}
    ).json()
    assert ok["result"]["parts"][0]["data"]["result"][0]["units"] > 0
    assert client.post("/a2a", content=b"{", headers={"Authorization": "Bearer demo-north"}).json()["error"]["code"] == -32700
