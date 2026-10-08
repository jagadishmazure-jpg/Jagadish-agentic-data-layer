"""Originality check: report any run of N consecutive words (default 8) that appears both in the
repository's text files and in the given reference documents. Used before publishing to make
sure no reference wording was copied. The references themselves are never part of the repo.

    python scripts/overlap_check.py /path/to/refs/*.txt [--n 8]

Exit code 1 if any overlap is found."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".txt",
    ".json",
    ".jsonl",
    ".yml",
    ".yaml",
    ".bicep",
    ".hcl",
    ".toml",
    ".sql",
    ".sh",
    ".ps1",
    "",
}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:['’][a-z]+)?", text.lower())


def shingles(ws: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def repo_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [ROOT / f for f in out if (ROOT / f).suffix in TEXT_SUFFIXES and (ROOT / f).is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("refs", nargs="+")
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args()
    ref: dict[tuple[str, ...], str] = {}
    for r in a.refs:
        for sh in shingles(words(Path(r).read_text(errors="ignore")), a.n):
            ref.setdefault(sh, Path(r).name)
    hits = 0
    files = repo_files()
    for f in files:
        common = shingles(words(f.read_text(errors="ignore")), a.n) & ref.keys()
        for sh in sorted(common):
            hits += 1
            print(f"{f.relative_to(ROOT)}  <->  {ref[sh]}:  {' '.join(sh)}")
    print(f"checked {len(files)} files against {len(a.refs)} reference file(s), n={a.n}: {hits} overlapping {a.n}-word run(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
