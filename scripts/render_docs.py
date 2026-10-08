"""Keep command output in the docs honest.

Markdown files contain blocks like::

    <!-- output: value -->
    ```text
    ...whatever `adl value` prints...
    ```
    <!-- /output -->

This script runs the ``adl`` CLI with the arguments in the marker and rewrites the block with
the real output. Code excerpts work the same way: ``<!-- code: src/adl/x.py::name -->`` is
replaced with the current source of the top-level function, class or constant ``name`` (or the
whole file when no ``::name`` is given), so excerpts never drift from the code. ``--check`` exits 1 if any block is stale (CI runs it), so a number in a doc can
only change when the code that produces it changes.

    python scripts/render_docs.py          # rewrite blocks
    python scripts/render_docs.py --check  # fail if any block is out of date
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adl.cli import main as cli  # noqa: E402

BLOCK = re.compile(r"(<!-- output: (?P<args>[^>]+?) -->\n)(?P<body>.*?)(<!-- /output -->)", re.S)
CODE = re.compile(r"(<!-- code: (?P<ref>[^>]+?) -->\n)(?P<body>.*?)(<!-- /code -->)", re.S)
LANG = {
    ".py": "python",
    ".sql": "sql",
    ".toml": "toml",
    ".hcl": "hcl",
    ".yaml": "yaml",
    ".tf": "hcl",
    ".bicep": "bicep",
    ".json": "json",
    ".yml": "yaml",
    ".sh": "bash",
}
_cache: dict[str, str] = {}


def excerpt(ref: str) -> tuple[str, str]:
    path, _, name = ref.partition("::")
    f = ROOT / path
    src = f.read_text()
    lang = LANG.get(f.suffix, "text")
    if not name:
        return lang, src.rstrip("\n")
    lines = src.splitlines()
    if f.suffix != ".py":
        # HCL / Bicep: the block whose first line starts with ``name``, up to its closing brace
        for i, line in enumerate(lines):
            if line.startswith(name):
                depth, j = 0, i
                while True:
                    depth += lines[j].count("{") - lines[j].count("}")
                    if (depth <= 0 and j > i) or (depth == 0 and "{" in lines[i] and j == i and lines[i].rstrip().endswith("}")):
                        break
                    j += 1
                return lang, "\n".join(lines[i : j + 1])
        raise SystemExit(f"{ref}: no block starting with {name}")
    for node in ast.parse(src).body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if name in names:
            start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
            return lang, "\n".join(lines[start - 1 : node.end_lineno])
    raise SystemExit(f"{ref}: no top-level definition named {name}")


def run(args: str) -> str:
    if args not in _cache:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli(shlex.split(args))
        if rc:
            raise SystemExit(f"adl {args} exited {rc}")
        _cache[args] = buf.getvalue().rstrip("\n")
    return _cache[args]


def render(text: str) -> str:
    def sub(m: re.Match) -> str:
        return f"{m.group(1)}```text\n{run(m.group('args'))}\n```\n{m.group(4)}"

    def code(m: re.Match) -> str:
        lang, body = excerpt(m.group("ref"))
        return f"{m.group(1)}```{lang}\n{body}\n```\n{m.group(4)}"

    return CODE.sub(code, BLOCK.sub(sub, text))


def md_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if not any(x in p.parts for x in (".git", ".venv", ".pytest_cache")))


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for p in md_files():
        old = p.read_text()
        if "<!-- output:" not in old and "<!-- code:" not in old:
            continue
        new = render(old)
        if new != old:
            stale.append(p.relative_to(ROOT))
            if not check:
                p.write_text(new)
    if check and stale:
        print("stale output blocks (run python scripts/render_docs.py):", *stale, sep="\n  ")
        return 1
    print(f"{'checked' if check else 'rendered'} output blocks; {len(stale)} file(s) {'stale' if check else 'updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
