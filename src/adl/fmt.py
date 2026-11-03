"""Plain-text formatting helpers shared by the CLI and the domain report modules.

Kept separate from adl.cli so the domain reports do not import the CLI (no import cycle).
"""

from __future__ import annotations


def table(rows: list[list], header: list[str]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    out = ["  ".join(h.ljust(w[i]) for i, h in enumerate(header)).rstrip(), "  ".join("-" * x for x in w)]
    out += ["  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    return "\n".join(out)


def usd(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def ci(lo: float, hi: float) -> str:
    return f"[{usd(lo)}, {usd(hi)}]"
