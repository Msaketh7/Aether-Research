"""The evaluation dataset: what a case is, and where cases come from.

A case is a question plus what a good answer to it would have to contain. It
is deliberately *not* an expected report: the system is non-deterministic by
construction - a model writes the prose and a critic decides how many rounds
to run - so scoring against a golden text would measure agreement with one
past run rather than quality (docs/evaluation.md §2).

What a case does assert is checkable without a model:

* **topics** the plan should have covered, so a planner that ignores half the
  question is visible as a number rather than as a feeling;
* **source types** the run should have reached, so a question about SEC
  filings answered entirely from blog posts fails;
* **claims** that must be present, named by a normalised key rather than by
  wording, because two correct runs phrase the same fact differently;
* the **expectation that a case is hard**, for the class of case whose correct
  behaviour is an honest low-coverage report rather than a confident one.

**Versioning is per file and pinned per run.** A metric that moved is either
the code or the data, and a result that does not record which dataset produced
it cannot tell you (``evaluations.dataset_version``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ResearchMode, SourceType

#: Where the shipped datasets live. Four parents up is the repository root -
#: apps/api/app/evaluations/dataset.py - the same walk ``app.core.config``
#: makes, and the same one to get wrong by one.
REPO_ROOT = Path(__file__).resolve().parents[4]
DATASET_DIR = REPO_ROOT / "data" / "eval" / "cases"

MAX_CASES = 200


class ExpectedClaim(BaseModel):
    """A fact a good answer has to contain, named rather than quoted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The normalised key the claim normalizer would produce, or a distinctive
    #: fragment of it. Matched case-insensitively as a substring, because two
    #: correct runs word the same claim differently and a key is the thing
    #: that is meant to be stable.
    key: str = Field(min_length=2, max_length=300)
    must_be_present: bool = True
    note: str = Field(default="", max_length=500)


class EvaluationCase(BaseModel):
    """One question, and what a good answer to it would contain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    question: str = Field(min_length=10, max_length=4000)
    mode: ResearchMode = ResearchMode.DEEP
    #: Lower-cased words or phrases the plan should have covered.
    expected_topics: tuple[str, ...] = Field(default=(), max_length=20)
    expected_source_types: tuple[SourceType, ...] = Field(default=(), max_length=5)
    expected_claims: tuple[ExpectedClaim, ...] = Field(default=(), max_length=20)
    #: What this case is testing, in one phrase, for the report.
    checks: Literal[
        "factual",
        "comparison",
        "contradiction",
        "no-good-sources",
    ] = "factual"
    notes: str = Field(default="", max_length=1000)


class Dataset(BaseModel):
    """A versioned set of cases, loaded from one file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^[a-z0-9][a-z0-9.\-]{0,59}$")
    description: str = Field(default="", max_length=2000)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=MAX_CASES)

    def case(self, case_id: str) -> EvaluationCase | None:
        return next((case for case in self.cases if case.id == case_id), None)


def load_dataset(path: Path) -> Dataset:
    """Read and validate one dataset file.

    Validated rather than trusted: a dataset is data, and a case with a
    misspelled field would otherwise be scored against silently - producing a
    metric that looks measured and is not.
    """
    return Dataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


def available(directory: Path = DATASET_DIR) -> list[Path]:
    """The dataset files that ship with the repository, in a stable order."""
    return sorted(directory.glob("*.json")) if directory.is_dir() else []
