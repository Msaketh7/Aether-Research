"""The project's license declarations agree, and none of them is open source.

The repository is proprietary. That is one decision recorded in eight places -
LICENSE, the README, pyproject.toml, four package.json files and the npm
lockfile - and scaffolding tools write "MIT" into a manifest by default. An
`npm init`, a copied template or a regenerated lockfile could quietly relicense
part of the project, and nobody reviews license metadata in a diff. This test
turns that into a failing build.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

MANIFESTS = [
    "package.json",
    "apps/web/package.json",
    "packages/shared-types/package.json",
    "scripts/package.json",
]

#: npm's documented value for "not licensed for use by others". Not to be
#: confused with "Unlicense", a public-domain dedication - the one typo that
#: would invert the meaning of the whole field.
NPM_PROPRIETARY = "UNLICENSED"


def test_the_license_file_reserves_all_rights():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")

    assert "All rights reserved" in text
    assert "Permission is hereby granted, free of charge" not in text, (
        "the MIT grant is back in LICENSE"
    )


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_every_package_manifest_is_unlicensed_and_private(manifest: str):
    data = json.loads((REPO / manifest).read_text(encoding="utf-8"))

    assert data.get("license") == NPM_PROPRIETARY
    # `private: true` makes `npm publish` refuse outright.
    assert data.get("private") is True


def test_the_npm_lockfile_agrees_with_the_manifests():
    """The lockfile records each workspace's license, and a stale "MIT" there
    is a contradiction sitting in the repository."""
    lock = json.loads((REPO / "package-lock.json").read_text(encoding="utf-8"))

    for key in ("", "apps/web", "packages/shared-types"):
        assert lock["packages"][key].get("license") == NPM_PROPRIETARY, key or "<root>"


def test_the_python_package_is_proprietary_and_unpublishable():
    project = tomllib.loads((REPO / "apps/api/pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert "Proprietary" in project["license"]["text"]
    # PyPI rejects any upload carrying this classifier.
    assert "Private :: Do Not Upload" in project.get("classifiers", [])


def test_third_party_portions_keep_their_attribution():
    """Claiming all rights over someone else's MIT code would be wrong, and it
    would weaken the position the proprietary license is enforced from."""
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "Copyright (c) 2023 shadcn" in notices
    assert "apps/web/src/components/ui" in notices
