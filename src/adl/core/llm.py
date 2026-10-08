"""Model client selection: a deterministic offline stand-in by default, Microsoft Foundry when asked.

ADL_LLM=foundry switches agents to `FoundryChatClient` from agent-framework-foundry (install the
`foundry` extra) with `DefaultAzureCredential`, so a managed identity is used in Azure and no key is
ever read. That path is written but has never been run from this repository.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from agent_framework import BaseChatClient


def estimate_tokens(text: str) -> int:
    """About four characters per token; good enough for budgeting, not for billing."""
    return max(1, len(text) // 4)


def get_chat_client(mock_factory: Callable[[], BaseChatClient]) -> BaseChatClient:
    if os.environ.get("ADL_LLM", "mock") != "foundry":
        return mock_factory()
    from agent_framework_foundry import FoundryChatClient  # pragma: no cover - needs the foundry extra and Azure
    from azure.identity import DefaultAzureCredential  # pragma: no cover

    return FoundryChatClient(  # pragma: no cover
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ.get("FOUNDRY_MODEL", "gpt-5-mini"),
        credential=DefaultAzureCredential(),
    )
