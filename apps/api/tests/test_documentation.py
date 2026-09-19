"""The documentation points at things that exist, and says what is true.

Documentation rots silently. A file is renamed and six links die; a phase lands
and the README still describes the phase before it; a screenshot is embedded and
then the file is never committed. None of that fails a build, and none of it is
visible in a diff of the file that broke - which is why it is checked here
instead.

These are deliberately structural checks. No test can tell whether a paragraph
is *true*; every one below asserts something a rename or a deletion would break,
which is the class of staleness that happens without anyone deciding to let it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: Directories whose Markdown is not ours to hold to this standard.
IGNORED = {"node_modules", ".git", ".venv", "__pycache__", ".next", ".data", "dist"}

#: `[label](target)`, ignoring a trailing `"title"`.
MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: `![alt](path)` - the same shape, but the target must be a file we ship.
MARKDOWN_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)\)")


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in REPO.rglob("*.md")
        if not any(part in IGNORED for part in path.relative_to(REPO).parts)
    )


def relative_links(path: Path) -> list[tuple[int, str]]:
    """Every link in ``path`` that names a file rather than a URL or an anchor."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in MARKDOWN_LINK.finditer(line):
            target = match.group("target")
            if target.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            found.append((number, target))
    return found


def test_every_relative_link_in_the_documentation_resolves():
    # Found six broken links when first written: docs/load-testing.md pointed at
    # `0008-cloud-deployment.md` (the file is `0008-aws-ecs-deployment.md`), and
    # the two frozen v1.0 documents still linked to their siblings as though
    # they had not been moved into docs/versions/.
    broken: list[str] = []

    for path in markdown_files():
        for number, target in relative_links(path):
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(REPO).as_posix()}:{number} -> {target}")

    assert not broken, "documentation links that go nowhere:\n  " + "\n  ".join(broken)


def test_every_in_page_link_points_at_a_heading_that_exists():
    """A table of contents rots the moment a section is renamed.

    Renaming the README's "Infrastructure" section to "System topology" is what
    prompted this: nothing about a rename makes the links to it fail loudly, and
    a dead anchor silently scrolls the reader nowhere.
    """

    def slug(heading: str) -> str:
        """GitHub's anchor rule: lowercase, drop punctuation, spaces to hyphens."""
        lowered = re.sub(r"[^\w\s-]", "", heading.strip().lower())
        return re.sub(r"\s+", "-", lowered)

    broken: list[str] = []

    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        headings = {slug(m.group(1)) for m in re.finditer(r"^#{1,6} (.+)$", text, re.M)}
        for number, line in enumerate(text.splitlines(), start=1):
            for anchor in re.findall(r"\]\(#([^)]+)\)", line):
                if anchor not in headings:
                    broken.append(f"{path.relative_to(REPO).as_posix()}:{number} -> #{anchor}")

    assert not broken, "in-page links with no heading behind them:\n  " + "\n  ".join(broken)


def test_every_embedded_image_is_a_file_that_is_committed():
    # A README that embeds a screenshot nobody committed renders as a broken
    # image on GitHub, which is worse than having no screenshot at all.
    missing: list[str] = []

    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_IMAGE.finditer(text):
            target = match.group("target")
            if target.startswith(("http://", "https://", "data:")):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append(f"{path.relative_to(REPO).as_posix()} -> {target}")

    assert not missing, "embedded images with no file behind them:\n  " + "\n  ".join(missing)


def test_the_readme_carries_the_screenshots_the_phase_requires():
    """`make screenshots` writes these; the README must show them."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    shots = REPO / "docs" / "screenshots"

    captured = {path.name for path in shots.glob("*.png")}
    assert captured, "no screenshots in docs/screenshots - run `make screenshots`"

    unreferenced = sorted(name for name in captured if f"docs/screenshots/{name}" not in readme)
    assert not unreferenced, (
        f"screenshots nothing links to: {unreferenced}. Either embed them or delete them - "
        "a file kept because it might be useful is the thing that goes stale."
    )


def test_the_adr_index_lists_every_adr():
    # The index is how anyone finds these. A new ADR that is not in it is an
    # ADR nobody reads, and adding the file is the half people remember.
    adrs = REPO / "docs" / "ADRs"
    index = (adrs / "README.md").read_text(encoding="utf-8")

    records = sorted(path.name for path in adrs.glob("0*.md"))
    assert len(records) >= 21, f"expected at least the 21 accepted ADRs, found {len(records)}"

    unlisted = [name for name in records if f"({name})" not in index]
    assert not unlisted, f"ADRs missing from docs/ADRs/README.md: {unlisted}"


def test_the_readme_does_not_claim_dependencies_the_project_does_not_have():
    """A tech-stack table naming a framework we never installed is a lie a
    reader cannot check without cloning the repo.

    LangChain and Celery were both in this table for twenty-four phases without
    ever being dependencies - the first because the plan once assumed it, the
    second because "Redis queue" reads like Celery. Each is now named in the
    README only to say why it is *absent*, and this test fails if one comes back
    as a claim.
    """
    import tomllib

    pyproject = tomllib.loads((REPO / "apps" / "api" / "pyproject.toml").read_text("utf-8"))
    declared = {
        re.split(r"[><=!\[]", name)[0].strip().lower()
        for name in pyproject["project"]["dependencies"]
    }

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("## Tech stack") : readme.index("## Frontend architecture")]

    for absent in ("langchain", "celery", "arq"):
        assert absent not in declared, (
            f"{absent} is a dependency now - the README's absence note is out of date"
        )
        # Named in prose explaining why it is not used is fine; named in the
        # table's "Choice" column is a claim.
        rows = [line for line in table.splitlines() if line.startswith("|")]
        claimed = [line for line in rows if absent in line.lower()]
        assert not claimed, (
            f"the tech-stack table claims {absent}, which is not installed:\n{claimed}"
        )


@pytest.mark.parametrize(
    "document",
    [
        "README.md",
        "docs/PHASES.md",
        "docs/PRD.md",
        "docs/TDD.md",
        "docs/architecture.md",
        "docs/evaluation.md",
        "docs/threat-model.md",
        "docs/load-testing.md",
    ],
)
def test_the_required_documents_exist_and_are_not_stubs(document: str):
    # Phase 25 names these. A document that exists as a heading and a TODO
    # satisfies a file-existence check and nothing a reader wants.
    path = REPO / document
    assert path.exists(), f"{document} is required by the build plan"
    assert len(path.read_text(encoding="utf-8")) > 4_000, f"{document} is a stub"
