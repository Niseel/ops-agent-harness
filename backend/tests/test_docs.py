"""The review guide and the main docs point at things that exist (AC-17). File reads only."""

import re

import pytest

from app.config import ROOT

GUIDE = (ROOT / "docs" / "REVIEW_GUIDE.md").read_text(encoding="utf-8")
TESTS = ROOT / "backend" / "tests"
SPANS = re.findall(r"`([^`\n]+)`", GUIDE)
FROM_ROOT = ("backend/", "frontend/", "evals/", "data/", "docs/", ".github/")


def test_review_guide_tests_exist():
    """`test_x.py::name` is a test in backend/tests; a bare `::name` uses the last file named; `::*` needs the file,
    `name*` a test with that prefix. `x.spec.ts` is a spec in frontend/src/app."""
    checked, last = 0, None
    for span in SPANS:
        for file, name in re.findall(r"(test_\w+\.py)?::([\w*]+)", span):
            last = file or last
            assert last, f"`{span}`: no test file named before it"
            source = (TESTS / last).read_text(encoding="utf-8")
            if name != "*":
                pattern = rf"^(async )?def {re.escape(name.rstrip('*'))}" + ("" if name.endswith("*") else r"\(")
                assert re.search(pattern, source, re.MULTILINE), f"`{span}`: no {name} in {last}"
            checked += 1
        for file in re.findall(r"^(test_\w+\.py)$", span):
            assert (TESTS / file).is_file(), f"`{span}`: no such test file"
            last = file
        for spec in re.findall(r"^([\w-]+\.spec\.ts)$", span):
            assert (ROOT / "frontend" / "src" / "app" / spec).is_file(), f"`{span}`: no such spec"
    assert checked > 30  # the matrix names its tests


def _defines(path, symbol: str) -> bool:
    """The file defines the symbol's last part; for `Class.member` it also defines the class."""
    source = path.read_text(encoding="utf-8")
    *owner, name = symbol.split(".")
    if path.suffix == ".py":  # a def or class at any depth, or a module-level assignment (not a keyword argument)
        pattern = rf"^\s*((async )?def|class) {name}\b|^{name}\s*[:=]"
    else:  # TypeScript: a class, function or const, or a method or field of a class
        pattern = rf"\b(class|function|const|interface|type) {name}\b|^\s*(readonly |protected |async )*{name}\s*[(=<]"
    classes = [rf"^(export )?class {part}\b" for part in owner]
    return all(re.search(p, source, re.MULTILINE) for p in [pattern, *classes])


def test_review_guide_symbols_exist():
    """`path.py:Symbol` or `path.ts:Symbol`: the file exists (from the repo root for backend/, frontend/, evals/, data/,
    docs/ and .github/, else under backend/app/) and defines the symbol, and for `Class.member` the class."""
    found = [re.fullmatch(r"([\w./-]+\.(?:py|ts)):([A-Za-z_][\w.]*)", span) for span in SPANS]
    found = [m for m in found if m]
    assert len(found) > 20
    for match in found:
        path, symbol = match.groups()
        file = ROOT / path if path.startswith(FROM_ROOT) else ROOT / "backend" / "app" / path
        assert file.is_file(), f"`{match[0]}`: no file {file.relative_to(ROOT)}"
        assert _defines(file, symbol), f"`{match[0]}`: {symbol} not defined"


@pytest.mark.parametrize("doc", ["README.md", "docs/DESIGN.md", "docs/REVIEW_GUIDE.md", "specs/ops-agent-harness.md"])
def test_doc_links_resolve(doc):
    path = ROOT / doc
    # [text](target), [text](<target>) and [text](target "title")
    for target in re.findall(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)", path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        assert (path.parent / target.split("#")[0]).exists(), f"{doc}: broken link {target}"
