"""Knowledge layer: hashed TF-IDF vectors, the entity graph, hybrid retrieval, trimming and the Azure AI Search adapter."""

import numpy as np
import pytest

from adl.domains.retail import runtime
from adl.knowledge.azure_search import AzureSearchKnowledge
from adl.knowledge.embed import HashedTfidf
from adl.knowledge.retrieve import mrr, ndcg_at, recall_at


@pytest.fixture(scope="module")
def ev():
    return runtime.retrieval_eval()


def test_hybrid_beats_vector_and_graph(ev):
    assert ev["hybrid"]["recall@5"] > ev["vector"]["recall@5"] > ev["graph"]["recall@5"]
    assert ev["hybrid"]["recall@5"] >= 0.9 and ev["hybrid"]["hit@5"] == 1.0


def test_metric_functions():
    assert recall_at(["a", "b", "c"], {"b", "z"}, 2) == 0.5
    assert mrr(["x", "a"], {"a"}) == 0.5
    assert ndcg_at(["a"], {"a"}, 5) == 1.0


def test_embedding_is_deterministic_and_normalised():
    e1, e2 = HashedTfidf(["spinach delivery late", "bagels promotion"]), HashedTfidf(["spinach delivery late", "bagels promotion"])
    v = e1("late spinach")
    assert np.allclose(v, e2("late spinach")) and np.isclose(np.linalg.norm(v), 1.0)


def test_graph_links_aliases_to_entities(lake):
    ents = lake.index.graph.link("Is Copperleaf late with baby spinach at Ashby Cross?")
    assert {"supplier:SUP-CP", "product:SKU-PR03", "store:S03"} <= ents


def test_north_copilot_never_sees_south_store_documents(lake):
    hits = lake.index.search("Southgate Riverside Oakhurst Fenwick store notes", k=10, regions=("north",))
    south = {"STORE-S05", "STORE-S06", "STORE-S07", "STORE-S08"}
    assert not south & {h.doc_id for h in hits}


def test_untrusted_documents_come_back_quoted_and_flagged(lake):
    hits = lake.index.search("Copperleaf message about orders", k=10)
    inj = [h for h in hits if h.doc_id == "MSG-CP-2"]
    assert inj and inj[0].injection_flag and inj[0].text.startswith("<untrusted_data>")


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body, headers))
        return {"value": [{"id": "SOP-ORDER"}]}


def test_azure_search_uses_a_bearer_token_never_a_key():
    rec = Recorder()
    ks = AzureSearchKnowledge("https://srch-adl.search.windows.net", "retail-knowledge", rec, token=lambda scope: f"tok:{scope}")
    ks.search("late delivery", [0.0] * 4, 3, ("north",))
    _method, _url, body, headers = rec.calls[0]
    assert headers["Authorization"].startswith("Bearer tok:https://search.azure.com") and "api-key" not in {h.lower() for h in headers}
    assert body["filter"] == "regions/any(r: r eq 'north' or r eq 'all')" and body["vectorQueries"][0]["k"] == 3


def test_azure_search_index_definition_and_validation():
    ks = AzureSearchKnowledge("https://srch-adl.search.windows.net", "retail-knowledge", Recorder(), token=lambda s: "t")
    names = {f["name"] for f in ks.index_definition()["fields"]}
    assert {"embedding", "regions", "entities"} <= names
    with pytest.raises(ValueError):
        ks.region_filter(("north' or 1 eq 1",))
    with pytest.raises(ValueError):
        AzureSearchKnowledge("http://insecure", "x", Recorder())


def test_knowledge_search_through_the_gateway_is_trimmed(gw):
    hits = gw.search_knowledge("agent:store-copilot-south", "Harbour Millbrook store notes", "store_operations", k=10)
    assert not {"STORE-S01", "STORE-S02", "STORE-S03", "STORE-S04"} & {h["doc_id"] for h in hits}
    assert gw.audit.events("knowledge.read")
