"""Azure AI Search adapter for the knowledge layer (written and tested with a fake transport; never run).

It builds the three requests the local index answers offline: the index definition (keyword fields,
a 2,048-dimension vector field with an HNSW profile and a semantic configuration), document upload
batches, and a hybrid query (BM25 text plus a vector query, fused by the service with RRF) with a
security filter on regions. Authentication is a Microsoft Entra token for the managed identity
(scope https://search.azure.com/.default) with key-based auth disabled on the service (see infra/).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from adl.knowledge.embed import DIM
from adl.knowledge.retrieve import Doc

API_VERSION = "2024-07-01"
SCOPE = "https://search.azure.com/.default"


class Transport(Protocol):
    def __call__(self, method: str, url: str, body: dict[str, Any] | None, headers: dict[str, str]) -> dict[str, Any]: ...


class TokenProvider(Protocol):
    def __call__(self, scope: str) -> str: ...


def managed_identity_token(scope: str) -> str:  # pragma: no cover - needs Azure
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(scope).token


@dataclass
class AzureSearchKnowledge:
    endpoint: str
    index: str
    transport: Transport
    token: TokenProvider = managed_identity_token

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("Azure AI Search endpoint must be https")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token(SCOPE)}", "Content-Type": "application/json"}
        return self.transport(method, f"{self.endpoint.rstrip('/')}{path}?api-version={API_VERSION}", body, headers)

    def index_definition(self) -> dict[str, Any]:
        return {
            "name": self.index,
            "fields": [
                {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                {"name": "title", "type": "Edm.String", "searchable": True},
                {"name": "content", "type": "Edm.String", "searchable": True},
                {"name": "doc_type", "type": "Edm.String", "filterable": True, "facetable": True},
                {"name": "regions", "type": "Collection(Edm.String)", "filterable": True},
                {"name": "entities", "type": "Collection(Edm.String)", "filterable": True},
                {"name": "embedding", "type": "Collection(Edm.Single)", "searchable": True, "dimensions": DIM, "vectorSearchProfile": "hnsw-profile"},
            ],
            "vectorSearch": {"algorithms": [{"name": "hnsw", "kind": "hnsw"}], "profiles": [{"name": "hnsw-profile", "algorithm": "hnsw"}]},
            "semantic": {
                "configurations": [
                    {
                        "name": "default",
                        "prioritizedFields": {"titleField": {"fieldName": "title"}, "prioritizedContentFields": [{"fieldName": "content"}]},
                    }
                ]
            },
        }

    def create_index(self) -> dict[str, Any]:
        return self._call("PUT", f"/indexes/{self.index}", self.index_definition())

    def upload(self, docs: list[Doc], vectors: list[list[float]], entities: dict[str, set[str]]) -> dict[str, Any]:
        batch = [
            {
                "@search.action": "mergeOrUpload",
                "id": d.id,
                "title": d.title,
                "content": d.text,
                "doc_type": d.type,
                "regions": list(d.regions) or ["all"],
                "entities": sorted(entities.get(d.id, set())),
                "embedding": v,
            }
            for d, v in zip(docs, vectors, strict=True)
        ]
        return self._call("POST", f"/indexes/{self.index}/docs/index", {"value": batch})

    @staticmethod
    def region_filter(regions: tuple[str, ...]) -> str:
        for r in regions:
            if not r.isalpha():
                raise ValueError(f"bad region {r!r}")
        parts = " or ".join(f"r eq '{r}'" for r in (*regions, "all"))
        return f"regions/any(r: {parts})"

    def hybrid_query(self, text: str, vector: list[float], k: int, regions: tuple[str, ...]) -> dict[str, Any]:
        return {
            "search": text,
            "top": k,
            "filter": self.region_filter(regions),
            "vectorQueries": [{"kind": "vector", "vector": vector, "fields": "embedding", "k": k}],
            "select": "id,title,content,doc_type",
        }

    def search(self, text: str, vector: list[float], k: int, regions: tuple[str, ...]) -> list[dict[str, Any]]:
        res = self._call("POST", f"/indexes/{self.index}/docs/search", self.hybrid_query(text, vector, k, regions))
        return res.get("value", [])
