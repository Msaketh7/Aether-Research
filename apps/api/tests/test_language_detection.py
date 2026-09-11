"""Language detection: confident answers recorded, guesses not."""

from __future__ import annotations

import pytest

from app.retrieval.language import detect_language
from tests.support.documents import prose

FRENCH = (
    "Le chiffre d'affaires de l'entreprise a fortement augmenté au cours du trimestre, "
    "selon les résultats publiés mardi. Les analystes estiment que la demande restera "
    "soutenue pendant toute l'année."
)
GERMAN = (
    "Der Umsatz des Unternehmens ist im Quartal deutlich gestiegen, wie aus den am "
    "Dienstag veröffentlichten Zahlen hervorgeht. Analysten erwarten, dass die Nachfrage "
    "im Laufe des Jahres hoch bleibt."
)


@pytest.mark.parametrize(("text", "expected"), [(prose(3), "en"), (FRENCH, "fr"), (GERMAN, "de")])
def test_prose_is_classified_with_confidence(text, expected):
    detection = detect_language(text)
    assert detection.language == expected
    assert detection.method == "detected"
    assert detection.confidence is not None
    assert detection.confidence >= 0.8


def test_numbers_are_not_a_language():
    """The classifier calls this Serbian at 1.4%. Recording that would be a lie
    a later stage would believe."""
    detection = detect_language("12 34 56 78 90 " * 20)
    assert detection.language is None
    assert detection.method == "unknown"


def test_the_documents_own_claim_is_used_when_detection_cannot_decide():
    detection = detect_language("12 34 56 78 90 " * 20, declared="en")
    assert (detection.language, detection.method) == ("en", "declared")


def test_a_confident_detection_outranks_the_documents_claim():
    """A page mislabelled lang="en" that is written in French is French."""
    detection = detect_language(FRENCH, declared="en")
    assert (detection.language, detection.method) == ("fr", "detected")
