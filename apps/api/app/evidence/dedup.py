"""Grouping a run's sources so the same content is counted once.

Corroboration is the number the whole product rests on: a claim backed by four
sources reads as established, and a claim backed by one reads as a lead. A wire
story republished by four outlets is one source's word four times, and counting
it as four is not a rounding error - it is the system telling a reader something
false in the direction they are least likely to check.

So a claim's corroboration is counted in *clusters*, not rows, and this decides
what a cluster is. Three rules, tried strongest first, and the rule that grouped
a cluster is recorded on it so a reader can see why two sources were collapsed:

``exact_hash``
    Identical normalised text. Certain: the same bytes produced the same digest.

``canonical_url``
    The same page after tracking parameters are stripped, fetched twice with
    different text - a live page that changed between fetches, or a paywall
    interstitial. Still one source.

``near_duplicate``
    Heavily overlapping text. A judgement, and the only one here that can be
    wrong, so it is deliberately hard to trigger (see ``NEAR_DUPLICATE_RATIO``).

**Wrong in the safe direction.** A false merge costs a claim one unit of
corroboration it might have deserved; a false split invents corroboration that
was never there. The first understates confidence, the second manufactures it,
and only one of those misleads a reader - so every threshold here is set to
merge reluctantly, and clustering never spans two runs.

The clusters are derived, not stored as their own entity: ``sources`` carries a
``dedup_cluster_id``, and the id is a UUID5 over the run and the primary source,
so re-running this over the same sources reproduces the same clusters rather
than renumbering them every time the projection runs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

#: Namespace for derived cluster ids. Fixed: changing it renumbers every
#: cluster in every stored run.
_CLUSTER_NAMESPACE = uuid.UUID("5f0e3a2c-9b3d-4f61-8a2e-1d7c6b4f9a20")

ClusterReason = Literal["exact_hash", "canonical_url", "near_duplicate"]

#: How much of the shorter text's trigrams must appear in the longer one for the
#: two to be one source. Containment rather than Jaccard, which was tried first
#: and is wrong for this input: what is compared is the stored excerpt, a fixed
#: 280 characters, so one copy carrying a byline or an editor's note pushes the
#: end of the other's text out of the window. Jaccard counts that difference
#: against both texts twice and a reprint of a wire story scores around 0.82 -
#: under any threshold high enough to be safe. Containment asks the question
#: that is actually being asked: is one of these texts inside the other.
NEAR_DUPLICATE_RATIO = 0.90

#: Trigrams a text needs before it may be compared at all. Containment is easy to
#: satisfy with a short string - a cookie banner is wholly inside every page that
#: shows one - so the floor is a real paragraph rather than a phrase. An excerpt
#: is 280 characters, about forty trigrams, so this excludes stubs and little
#: else.
MIN_SHINGLES = 25

#: Texts compared pairwise per run. Comparison is quadratic and each one is a set
#: intersection, so a run that somehow gathered thousands of sources would spend
#: real time here. Beyond this the exact rules still apply and near-duplicate
#: detection is skipped, which loses a merge rather than producing a wrong one.
MAX_NEAR_DUPLICATE_CANDIDATES = 400


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """What clustering needs to know about one source. No document text is read.

    ``text`` is the stored excerpt - the opening of the normalised document -
    rather than the whole of it. That is a deliberate limit: it catches a
    syndicated copy, which is identical from its first sentence, and it does not
    catch a paraphrase, which nothing here claims to catch. Loading every
    document's full text to compare them would make a projection cost as much as
    the research did.
    """

    source_id: uuid.UUID
    canonical_url: str
    content_hash: str
    title: str
    text: str
    #: Decides which source represents the cluster: the first one fetched is the
    #: one the run actually used, and later copies are the duplicates.
    accessed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class Cluster:
    """One group of sources holding the same content."""

    cluster_id: uuid.UUID
    primary_source_id: uuid.UUID
    duplicate_source_ids: tuple[uuid.UUID, ...]
    reason: ClusterReason

    @property
    def source_ids(self) -> tuple[uuid.UUID, ...]:
        return (self.primary_source_id, *self.duplicate_source_ids)


def cluster_sources(run_id: uuid.UUID, fingerprints: Sequence[SourceFingerprint]) -> list[Cluster]:
    """Group a run's sources. Every source is in exactly one cluster.

    Singletons are included: a source that duplicates nothing is a cluster of
    one, which is what makes "count the clusters" a complete rule for
    corroboration rather than one with an exception in it.
    """
    if not fingerprints:
        return []

    ordered = sorted(fingerprints, key=lambda f: (f.accessed_at, str(f.source_id)))
    #: Fetch order, which decides which source represents a merged cluster.
    position = {f.source_id: index for index, f in enumerate(ordered)}
    parent = {f.source_id: f.source_id for f in ordered}
    reasons: dict[uuid.UUID, ClusterReason] = {}

    def find(node: uuid.UUID) -> uuid.UUID:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: uuid.UUID, right: uuid.UUID, reason: ClusterReason) -> None:
        """Merge two components, recording the rule that actually joined them.

        Nothing happens when they are already one: the rules run strongest
        first, so a pair a certain rule has already matched is not re-described
        by a weaker one that also happens to match it. What a cluster is
        reported as is the weakest rule that *made* a merge in it - a group held
        together partly by a digest and partly by a judgement is only as certain
        as the judgement in it.
        """
        a, b = find(left), find(right)
        if a == b:
            return
        # The root fetched first stays the root, so a cluster's primary is the
        # copy the run actually read and the rest are its duplicates.
        keep, drop = (a, b) if position[a] <= position[b] else (b, a)
        parent[drop] = keep
        reasons[keep] = _weaker(_weaker(reasons.get(keep), reasons.get(drop)), reason) or reason

    by_hash: dict[str, uuid.UUID] = {}
    for fingerprint in ordered:
        if (seen := by_hash.get(fingerprint.content_hash)) is not None:
            union(seen, fingerprint.source_id, "exact_hash")
        else:
            by_hash[fingerprint.content_hash] = fingerprint.source_id

    by_url: dict[str, uuid.UUID] = {}
    for fingerprint in ordered:
        if (seen := by_url.get(fingerprint.canonical_url)) is not None:
            union(seen, fingerprint.source_id, "canonical_url")
        else:
            by_url[fingerprint.canonical_url] = fingerprint.source_id

    for left, right in _near_duplicates(ordered):
        union(left, right, "near_duplicate")

    groups: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fingerprint in ordered:
        groups.setdefault(find(fingerprint.source_id), []).append(fingerprint.source_id)

    clusters: list[Cluster] = []
    for root, members in groups.items():
        primary = root
        duplicates = tuple(member for member in members if member != primary)
        clusters.append(
            Cluster(
                cluster_id=cluster_identity(run_id, primary),
                primary_source_id=primary,
                duplicate_source_ids=duplicates,
                # A cluster of one was grouped by nothing. It is recorded as an
                # exact match with itself rather than given a fourth reason that
                # would have to mean "none" everywhere it is read.
                reason=reasons.get(root, "exact_hash") if duplicates else "exact_hash",
            )
        )
    clusters.sort(key=lambda cluster: position[cluster.primary_source_id])
    return clusters


def cluster_identity(run_id: uuid.UUID, primary_source_id: uuid.UUID) -> uuid.UUID:
    """A cluster's id, derived so that re-clustering does not renumber it."""
    return uuid.uuid5(_CLUSTER_NAMESPACE, f"{run_id}:{primary_source_id}")


#: Reasons from weakest to strongest. A cluster is reported as the weakest rule
#: holding any part of it together.
_STRENGTH: tuple[ClusterReason, ...] = ("near_duplicate", "canonical_url", "exact_hash")


def _weaker(left: ClusterReason | None, right: ClusterReason | None) -> ClusterReason | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if _STRENGTH.index(left) < _STRENGTH.index(right) else right


def _near_duplicates(
    fingerprints: Sequence[SourceFingerprint],
) -> Iterable[tuple[uuid.UUID, uuid.UUID]]:
    """Pairs where one text is contained in the other, compared pairwise.

    No index and no minhash: the candidate set is bounded above, a run's sources
    are tens rather than millions, and an approximate index here would add a
    second way to be wrong about something the whole design is trying to be
    careful about.
    """
    if len(fingerprints) > MAX_NEAR_DUPLICATE_CANDIDATES:
        return
    shingled = [
        (fingerprint.source_id, _shingles(f"{fingerprint.title} {fingerprint.text}"))
        for fingerprint in fingerprints
    ]
    comparable = [(source_id, grams) for source_id, grams in shingled if len(grams) >= MIN_SHINGLES]
    for index, (left_id, left) in enumerate(comparable):
        for right_id, right in comparable[index + 1 :]:
            if _containment(left, right) >= NEAR_DUPLICATE_RATIO:
                yield left_id, right_id


def _shingles(text: str) -> frozenset[str]:
    """Word trigrams, lowercased. Word-level rather than character-level so that
    a different byline or date on an otherwise identical article moves the ratio
    by a few trigrams instead of shifting every window in the text."""
    words = text.casefold().split()
    if len(words) < 3:
        return frozenset()
    return frozenset(" ".join(words[i : i + 3]) for i in range(len(words) - 2))


def _containment(left: frozenset[str], right: frozenset[str]) -> float:
    """How much of the shorter text appears in the longer one.

    Asymmetric by design, and normalised by the smaller side, so a text that is
    wholly inside another scores 1.0 however much the other adds around it.
    """
    smaller = min(len(left), len(right))
    return len(left & right) / smaller if smaller else 0.0
