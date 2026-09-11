"""Which language a document is written in (TDD 8.1).

Detected rather than assumed: the lexical index is built with the English
text-search configuration, and a German filing indexed as English stems wrongly
and ranks badly. Phase 8 reads ``documents.language`` to decide what to do
about that.

`py3langid` does the classifying: a Naive Bayes model bundled with the package,
deterministic and offline, written by the author of trafilatura - which already
does the HTML half of this pipeline.

A detection below the confidence floor is recorded as *unknown*, not as a guess.
"12 34 56 78 90" classifies as Serbian at 1.4% confidence, and storing that
would be worse than storing nothing, because a later stage would believe it.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

#: Below this, the classifier is guessing. Clear prose scores above 0.9.
MIN_CONFIDENCE = 0.80
#: Too few letters is no evidence at all: numbers, tables, a title page.
MIN_LETTERS = 40
#: Characters sampled from each of the start, middle and end.
_WINDOW = 4000


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    #: ISO 639-1 code, or ``None`` when it is not known.
    language: str | None
    #: The classifier's probability for its best answer, when it ran.
    confidence: float | None
    #: ``detected``, ``declared`` (the document's own claim), or ``unknown``.
    method: str


@functools.lru_cache(maxsize=1)
def _identifier() -> Any:
    # Loaded once, on first use: the model takes a second or two to read, and
    # the API process, which never ingests, should not pay for it.
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    return LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)


def detect_language(text: str, *, declared: str | None = None) -> LanguageDetection:
    """Classify ``text``, preferring a confident detection over the document's claim."""
    sample = _sample(text)
    confidence: float | None = None
    if sum(1 for character in sample if character.isalpha()) >= MIN_LETTERS:
        code, probability = _identifier().classify(sample)
        confidence = round(float(probability), 4)
        if confidence >= MIN_CONFIDENCE:
            return LanguageDetection(str(code), confidence, "detected")
    if declared:
        return LanguageDetection(declared, confidence, "declared")
    return LanguageDetection(None, confidence, "unknown")


def _sample(text: str) -> str:
    """The start, middle and end.

    A report with an English cover page and a French body should not be
    classified by its cover alone, and classifying all of a two-million
    character document would cost time for no extra certainty.
    """
    if len(text) <= 3 * _WINDOW:
        return text
    middle = len(text) // 2
    return " ".join(
        (text[:_WINDOW], text[middle - _WINDOW // 2 : middle + _WINDOW // 2], text[-_WINDOW:])
    )
