"""Cloud adapters: one conformance suite against every adapter, with fake clients, plus each platform's specifics."""

import pyarrow as pa
import pytest

from adl.storage import fake_adapters
from adl.storage.aws import _athena_literal
from adl.storage.base import TableStore
from adl.storage.databricks import AZURE_DATABRICKS_APP_ID
from adl.storage.local import LocalDeltaStore
from adl.storage.remote import positional_to_named

T = pa.table({"store_id": ["S01", "S02", "S05"], "units": [3, 5, 7], "region": ["north", "north", "south"]})
NAMES = list(fake_adapters())


@pytest.fixture(params=[*NAMES, "local"])
def store(request, tmp_path):
    return LocalDeltaStore(tmp_path) if request.param == "local" else fake_adapters()[request.param]


def test_implements_the_interface(store):
    assert isinstance(store, TableStore)


def test_write_read_round_trip(store):
    w = store.write("gold", "sales_daily", T)
    assert w.rows == 3 and store.read("gold", "sales_daily").num_rows == 3
    assert store.tables("gold") == ["sales_daily"]


def test_parameterised_sql(store):
    store.write("gold", "sales_daily", T)
    rows = store.sql(f"SELECT store_id FROM {store.qualified('gold', 'sales_daily')} WHERE region = ? AND units > ? ORDER BY store_id", ["north", 4])
    assert [r["store_id"] for r in rows] == ["S02"]


def test_a_value_cannot_become_sql(store):
    store.write("gold", "sales_daily", T)
    rows = store.sql(f"SELECT COUNT(*) AS n FROM {store.qualified('gold', 'sales_daily')} WHERE region = ?", ["north' OR '1'='1"])
    assert rows[0]["n"] == 0


@pytest.mark.parametrize("layer,table", [("platinum", "x"), ("gold", "x; DROP TABLE y"), ("gold", "1abc")])
def test_illegal_names_are_rejected(store, layer, table):
    with pytest.raises(ValueError):
        store.uri(layer, table)


@pytest.mark.parametrize("name", NAMES)
def test_cloud_adapters_are_secretless(name):
    a = fake_adapters()[name].auth()
    assert a.secretless and a.scopes
    text = (a.method + a.identity + a.notes).lower()
    assert "key" not in text.replace("no keys", "").replace("no access keys", "").replace("no service account key", "").replace(
        "no long-lived access keys", ""
    )


@pytest.mark.parametrize("name", NAMES)
def test_every_call_asks_for_a_token(name):
    st = fake_adapters()[name]
    st.write("gold", "t", T)
    st.sql(f"SELECT 1 AS x FROM {st.qualified('gold', 't')}")
    assert st.tokens.asked and st.tokens.asked[0] == st.scope


def test_onelake_uri_and_sql_endpoint():
    st = fake_adapters()["fabric-onelake"]
    assert st.uri("gold", "t") == "abfss://wf-data@onelake.dfs.fabric.microsoft.com/retail.Lakehouse/Tables/gold/t"
    st.write("gold", "t", T)
    st.sql(f"SELECT * FROM {st.qualified('gold', 't')}")
    assert st.client.requests[-1]["dialect"] == "tsql" and st.tokens.asked == [st.scope, st.sql_scope]


def test_databricks_uses_named_parameters():
    st = fake_adapters()["azure-databricks"]
    st.write("gold", "t", T)
    st.sql(f"SELECT * FROM {st.qualified('gold', 't')} WHERE store_id = ? AND units > ?", ["S01", 1])
    body = st.client.requests[-1]["body"]
    assert ":p0" in body["statement"] and ":p1" in body["statement"] and body["parameters"][1] == {"name": "p1", "value": "1"}
    assert st.scope.startswith(AZURE_DATABRICKS_APP_ID)


def test_positional_to_named_skips_quoted_question_marks():
    sql, names = positional_to_named("SELECT '?' AS q WHERE a = ? AND b = ?")
    assert sql == "SELECT '?' AS q WHERE a = :p0 AND b = :p1" and names == ["p0", "p1"]


def test_bigquery_load_job_and_typed_parameters():
    st = fake_adapters()["gcp-bigquery"]
    w = st.write("gold", "t", T, mode="append")
    assert w.requests[0]["load_job"]["writeDisposition"] == "WRITE_APPEND"
    st.sql(f"SELECT * FROM {st.qualified('gold', 't')} WHERE units > ? AND store_id = ?", [1, "S01"])
    types = [p["parameterType"]["type"] for p in st.client.requests[-1]["body"]["queryParameters"]]
    assert types == ["INT64", "STRING"]


def test_aws_glue_table_and_athena_literals():
    st = fake_adapters()["aws-s3-glue-athena"]
    w = st.write("gold", "t", T)
    cols = w.requests[0]["glue_table"]["TableInput"]["StorageDescriptor"]["Columns"]
    assert {"Name": "units", "Type": "bigint"} in cols
    assert _athena_literal("O'Brien") == "'O''Brien'" and _athena_literal(True) == "true" and _athena_literal(3) == "3"


def test_bad_write_mode_is_rejected():
    with pytest.raises(ValueError):
        fake_adapters()["aws-s3-glue-athena"].write("gold", "t", T, mode="merge")
