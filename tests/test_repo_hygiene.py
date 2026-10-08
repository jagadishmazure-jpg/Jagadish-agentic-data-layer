"""Repository hygiene: complete docs, current outputs, no dates, no real identifiers, honest claims."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / f
    for f in subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    if (ROOT / f).is_file()
]
MD = sorted(p for p in FILES if p.suffix == ".md")
SECTIONS = [
    "Purpose", "Architecture", "How it works", "Key files", "Code excerpts", "Configuration", "Commands",
    "Real output", "Tests and gates", "Guardrails", "Security and governance", "Observability",
    "Failure modes", "Mapping to cloud services", "Limitations", "Interview talking points",
]  # fmt: skip
FULL_DOCS = sorted(p for d in ("components", "infra", "mortgage", "insurance") for p in (ROOT / "docs" / d).glob("*.md") if p.name != "README.md")
SERVICES = ("Microsoft Fabric", "Entra ID", "BigQuery", "S3")
MONTHS = r"\b(January|February|March|April|June|July|August|September|October|November|December)\b"
SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".tf", ".bicep", ".hcl", ".sh", ".toml", ".jsonl", ".tfvars"}
TEXT_FILES = [p for p in FILES if p.suffix in SUFFIXES]
# Public, identical-in-every-tenant identifiers: Azure built-in role definitions (identity.bicep) and the
# first-party Azure Databricks application id (the token audience in storage/databricks.py).
ALLOWED_GUIDS = {
    "identity.bicep": {
        "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
        "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
        "4633458b-17de-408a-b874-0445c86b69e6",
        "1407120a-92aa-4202-b7e9-c0e197c71c8f",
    },
    "databricks.py": {"2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"},
    "test_adapters.py": {"2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"},
}
ALLOWED_GUIDS["test_iac.py"] = ALLOWED_GUIDS["identity.bicep"]
ARTICLE = "https://mitsloan.mit.edu/ideas-made-to-matter/what-leaders-still-get-wrong-about-ai"
ORGS = ("Wrenfield Grocers", "Quillmere Home Loans", "Ferrowind Insurance", "Halsey Vale Health")


def flat(path: Path) -> str:
    """File text with whitespace collapsed, so phrases still match across wrapped lines."""
    return " ".join(path.read_text().split())


def folders():
    return sorted({p.parent for p in FILES})


# .github has no README on purpose: GitHub would show .github/README.md instead of the root README.
@pytest.mark.parametrize("folder", [f for f in folders() if f != ROOT / ".github"], ids=lambda p: str(p.relative_to(ROOT)) or ".")
def test_every_folder_has_a_readme_with_a_file_table(folder):
    readme = folder / "README.md"
    assert readme.exists(), f"{folder} has no README.md"
    if folder != ROOT:
        assert "| File | What it does |" in readme.read_text()


def test_folder_readmes_list_every_child():
    tracked_dirs = {d for p in FILES for d in p.relative_to(ROOT).parents}
    for readme in (p for p in MD if p.name == "README.md" and p.parent != ROOT):
        text = readme.read_text()
        for child in readme.parent.iterdir():
            rel = child.relative_to(ROOT)
            if child.name == "README.md" or (child.is_file() and child not in FILES) or (child.is_dir() and rel not in tracked_dirs):
                continue
            name = child.name + ("/" if child.is_dir() else "")
            assert f"`{name}`" in text, f"{readme.relative_to(ROOT)} does not list {name}"


def test_no_dates_in_markdown():
    for p in MD:
        t = re.sub(r"@\d{4}-\d{2}-\d{2}(-preview)?", "", p.read_text())
        assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", t), f"ISO date in {p}"
        assert not re.search(r"(?<![\w$,.])20[1-3]\d(?![\w,.%])", t), f"year in {p}"
        assert not re.search(MONTHS, t), f"month name in {p}"
        assert "Date:" not in t, f"Date line in {p}"


def test_no_todos_or_placeholders():
    for p in MD:
        t = p.read_text()
        for word in ("TODO", "TBD", "FIXME", "lorem ipsum", "coming soon"):
            assert word not in t, f"{word} in {p}"


def test_full_doc_set_exists():
    names = {p.relative_to(ROOT / "docs").as_posix() for p in FULL_DOCS}
    assert len([n for n in names if n.startswith("components/")]) == 16
    assert len([n for n in names if n.startswith("infra/")]) == 5
    assert len([n for n in names if n.startswith("mortgage/")]) == 5
    assert len([n for n in names if n.startswith("insurance/")]) >= 5
    for top in (
        "architecture", "roadmap", "value-case", "mit-article-mapping", "ai-business-models", "operating-model",
        "data-governance", "purview-mapping", "threat-model", "observability", "failure-modes", "cloud-mapping",
        "metrics", "implementation-guide", "adding-a-domain", "interview-guide", "glossary", "deployment",
    ):  # fmt: skip
        assert (ROOT / "docs" / f"{top}.md").exists(), top


@pytest.mark.parametrize("doc", FULL_DOCS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_full_doc_has_all_sections_in_order(doc):
    t = doc.read_text()
    pos = [t.find(f"## {i}. {s}") for i, s in enumerate(SECTIONS, 1)]
    assert all(x >= 0 for x in pos), [s for s, x in zip(SECTIONS, pos, strict=True) if x < 0]
    assert pos == sorted(pos)
    assert "```mermaid" in t and "<!-- output:" in t
    assert "<!-- code:" in t or "```bash" in t
    mapping = t[pos[13] : pos[14]]
    assert "| Azure | Google Cloud | AWS |" in mapping
    for svc in SERVICES:
        assert svc in t, f"{doc.name} does not map to {svc}"


def test_doc_links_resolve():
    for p in MD:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", p.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (p.parent / target).resolve().exists(), f"{p.relative_to(ROOT)} links to missing {target}"


def test_doc_outputs_and_excerpts_are_current():
    r = subprocess.run([sys.executable, "scripts/render_docs.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_secrets_or_real_identifiers():
    guid = re.compile(r"\b(?!00000000-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
    bad = [
        re.compile(p, re.I)
        for p in (r"AccountKey=", r"-----BEGIN", r"client_secret\s*=", r"@gmail\.com", r"SharedAccessSignature=", r"AKIA[0-9A-Z]{16}")
    ]
    for p in TEXT_FILES:
        if p.name == "test_repo_hygiene.py":
            continue
        t = p.read_text(errors="ignore")
        found = {m.group(0).lower() for m in guid.finditer(t)}
        assert found <= ALLOWED_GUIDS.get(p.name, set()), f"GUID-like identifier in {p}: {sorted(found)}"
        for b in bad:
            assert not b.search(t), f"{b.pattern} in {p}"


@pytest.mark.parametrize("domain", ["retail", "mortgage", "insurance"])
def test_people_in_the_synthetic_data_use_reserved_contact_details(domain):
    text = (ROOT / f"src/adl/domains/{domain}/synth.py").read_text()
    for d in re.findall(r"@([a-z0-9.-]+\.[a-z]+)", text):
        assert d.endswith(".example"), d
    for phone in re.findall(r"\b\d{3}-\d{4}\b", text):
        assert phone.startswith("555-01") or phone.startswith("01"), phone


def test_reference_material_is_never_committed():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if f.lower().endswith((".pdf", ".html"))]
    assert "*.pdf" in (ROOT / ".gitignore").read_text() and "refs/" in (ROOT / ".gitignore").read_text()


def test_article_is_credited_by_link_and_paraphrased():
    readme = flat(ROOT / "README.md")
    mapping = flat(ROOT / "docs/mit-article-mapping.md")
    for t in (readme, mapping):
        assert ARTICLE in t and "MIT CISR" in t and "Beth Stackpole" in t
    assert "paraphrase" in mapping and "not quoted" in mapping


def test_organisations_are_fictional():
    readme = flat(ROOT / "README.md")
    assert "fictional" in readme
    for org in ORGS:
        assert org in readme


def test_readme_never_claims_deployment():
    t = flat(ROOT / "README.md").lower()
    assert "nothing is deployed" in t or "never been deployed" in t
    for claim in ("running in production", "live in production", "deployed to production", "in production at"):
        assert claim not in t


def test_readme_separates_built_from_planned():
    t = flat(ROOT / "README.md")
    assert "## Built vs planned" in t
    for word in ("Built", "Planned", "Written, not run"):
        assert word in t


def test_changelog_has_only_unreleased():
    versions = re.findall(r"^## (.+)$", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert versions == ["Unreleased"]


def test_six_adrs_without_date_lines():
    adrs = sorted((ROOT / "docs/adr").glob("0*.md"))
    assert len(adrs) == 6
    for a in adrs:
        t = a.read_text()
        assert "**Status:**" in t and "Date" not in t


def test_root_files_exist():
    for f in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE"):
        assert (ROOT / f).exists(), f


def test_security_policy_has_a_fallback_contact():
    t = (ROOT / "SECURITY.md").read_text()
    assert "Report a vulnerability" in t and "Security contact request" in t


def test_readme_test_count_is_the_real_count():
    t = (ROOT / "README.md").read_text()
    assert "## At a glance (for recruiters)" in t
    m = re.search(r"\*\*(\d+) automated tests\*\*", t)
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True)
    collected = re.search(r"(\d+) tests? collected", r.stdout)
    assert m and collected and int(m.group(1)) == int(collected.group(1)), (m and m.group(1), r.stdout[-200:])


def test_planned_domains_say_planned():
    for d in ("healthcare",):
        t = flat(ROOT / "domains" / d / "README.md")
        assert "planned" in t.lower() and "not built" in t.lower()
