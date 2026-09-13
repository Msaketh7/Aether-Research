"""Retrieval metrics, and the distinction the evaluation methodology turns on.

Every assertion here is arithmetic with a known answer. The ones that matter
most are about *unmeasurable*: a case with no labelled relevant chunk yields
``None``, never 0.0, and the mean says how many it dropped. Averaging an
unlabelled case in as zero is how an incomplete dataset quietly reports that
retrieval got worse.
"""

from __future__ import annotations

import math

import pytest

from app.retrieval.metrics import (
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from tests.support.retrieval import chunk_id


def ranked(*names: str):
    return [chunk_id(name) for name in names]


def relevant(*names: str):
    return {chunk_id(name) for name in names}


# --- recall ---------------------------------------------------------------


def test_recall_is_against_the_labelled_set_not_the_page_size():
    """Two of the three labelled chunks in the top 10 is 0.67, not 0.2."""
    value = recall_at_k(ranked("a", "x", "b", "y"), relevant("a", "b", "c"), 10)
    assert value == pytest.approx(2 / 3)


def test_recall_only_counts_what_fits_in_k():
    assert recall_at_k(ranked("x", "y", "a"), relevant("a"), 2) == 0.0
    assert recall_at_k(ranked("x", "y", "a"), relevant("a"), 3) == 1.0


def test_recall_of_an_unlabelled_case_is_not_measured():
    """Not zero. A case nobody labelled says nothing about the retriever."""
    assert recall_at_k(ranked("a"), relevant(), 10) is None


# --- precision ------------------------------------------------------------


def test_precision_divides_by_k_not_by_what_came_back():
    """Three relevant results out of a requested ten is 0.3.

    Dividing by the number returned would score a retriever that found three
    chunks and stopped as perfectly precise, hiding that it failed to fill the
    context it was asked for.
    """
    assert precision_at_k(ranked("a", "b", "c"), relevant("a", "b", "c"), 10) == pytest.approx(0.3)


def test_precision_at_the_size_actually_returned_is_one():
    assert precision_at_k(ranked("a", "b", "c"), relevant("a", "b", "c"), 3) == 1.0


# --- MRR ------------------------------------------------------------------


def test_reciprocal_rank_is_one_over_the_first_hit():
    assert reciprocal_rank(ranked("x", "y", "a"), relevant("a")) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_relevant_was_ranked():
    """Zero, not None: the case *was* labelled and the retriever missed it."""
    assert reciprocal_rank(ranked("x", "y"), relevant("a")) == 0.0


def test_reciprocal_rank_of_an_unlabelled_case_is_not_measured():
    assert reciprocal_rank(ranked("a"), relevant()) is None


# --- nDCG -----------------------------------------------------------------


def test_ndcg_is_one_when_the_ranking_could_not_be_better():
    assert ndcg_at_k(ranked("a", "b"), relevant("a", "b"), 10) == 1.0


def test_ndcg_penalises_a_correct_set_in_the_wrong_order():
    """The metric recall and precision cannot see: same chunks, worse ranking."""
    good = ndcg_at_k(ranked("a", "b", "x", "y"), relevant("a", "b"), 4)
    bad = ndcg_at_k(ranked("x", "y", "a", "b"), relevant("a", "b"), 4)
    assert good == 1.0
    assert bad is not None and bad < good


def test_ndcg_reads_graded_relevance_when_it_is_given():
    """A chunk that answers the question outranks one that merely touches it."""
    grades = {chunk_id("a"): 3.0, chunk_id("b"): 1.0}
    strong_first = ndcg_at_k(ranked("a", "b"), grades, 2)
    weak_first = ndcg_at_k(ranked("b", "a"), grades, 2)
    assert strong_first == 1.0
    assert weak_first is not None and weak_first < strong_first


def test_ndcg_normalises_against_what_the_labels_allow():
    """One of two relevant chunks, ranked first, is below 1: the other is missing."""
    value = ndcg_at_k(ranked("a", "x"), relevant("a", "b"), 2)
    expected = 1.0 / (1.0 + 1.0 / math.log2(3))
    assert value == pytest.approx(expected)


# --- aggregation ----------------------------------------------------------


def test_mean_drops_unmeasurable_cases_and_counts_them():
    result = mean([1.0, 0.0, None, 0.5])
    assert (result.value, result.measured, result.unmeasurable) == (0.5, 3, 1)


def test_mean_of_nothing_measurable_is_not_measured():
    """Reported as 'not measured', which is what the evaluation doc requires."""
    result = mean([None, None])
    assert result.value is None
    assert str(result) == "not measured"


def test_mean_prints_the_sample_size_beside_the_value():
    assert str(mean([1.0, 0.0])) == "0.500 (n=2)"
