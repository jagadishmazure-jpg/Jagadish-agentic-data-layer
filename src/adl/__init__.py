"""Agentic data layer: AI-ready data products that agents consume safely, with business value measured.

Package map:
    core/       domain-agnostic machinery: contracts, quality, lineage, metrics layer, access policy,
                audit, guardrails, offline model client, value ledger, FOCUS export
    storage/    one storage and query interface; local Delta + DuckDB adapter and cloud adapters
    knowledge/  embeddings, vector index, knowledge graph, GraphRAG-style retrieval, evaluation
    serve/      MCP data server and A2A endpoint over the governed gateway
    domains/    pluggable business domains (retail, mortgage, insurance and healthcare, all built)
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
