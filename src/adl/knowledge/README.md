# src/adl/knowledge

Knowledge layer: domain-neutral retrieval used through the gateway.

| File | What it does |
|---|---|
| `__init__.py` | Package marker |
| `embed.py` | Deterministic hashed TF-IDF embeddings |
| `graph.py` | Knowledge graph of entities, relationships and document mentions |
| `retrieve.py` | Vector, graph and hybrid ranking with region trimming; retrieval metrics |
| `azure_search.py` | Azure AI Search index, upload and hybrid query requests with Entra ID tokens (written, not run) |
