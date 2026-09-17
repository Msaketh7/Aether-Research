"""Source deduplication.

Pure functions over fingerprints, so these are the tests that can afford to be
exhaustive about the judgement calls: which rule wins, which source represents a
cluster, and - the one that matters most - that two different articles about the
same subject are *not* collapsed into one. A false merge costs a claim
corroboration it deserved; a false split invents corroboration that was never
there, and only the second misleads a reader.
"""

from __future__ import annotations

import datetime as dt
import uuid

from app.evidence.dedup import (
    MIN_SHINGLES,
    NEAR_DUPLICATE_RATIO,
    SourceFingerprint,
    cluster_identity,
    cluster_sources,
)

RUN = uuid.UUID("11111111-1111-4111-8111-111111111111")
BASE = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)

WIRE_STORY = (
    "The company said on Tuesday that quarterly data center revenue reached "
    "thirty five point six billion dollars, up from twenty two point six billion "
    "a year earlier, as demand for inference accelerators continued to outstrip "
    "supply across every region it serves."
)

DIFFERENT_STORY = (
    "Analysts at a research firm published a note arguing that inference pricing "
    "will fall through next year as new capacity arrives, and advised clients to "
    "delay long term commitments until the second half."
)


def fingerprint(
    name: str,
    *,
    url: str | None = None,
    digest: str | None = None,
    title: str = "Data center revenue",
    text: str = WIRE_STORY,
    minutes: int = 0,
) -> SourceFingerprint:
    return SourceFingerprint(
        source_id=uuid.uuid5(RUN, name),
        canonical_url=url or f"https://{name}.test/story",
        content_hash=digest or f"hash-{name}",
        title=title,
        text=text,
        accessed_at=BASE + dt.timedelta(minutes=minutes),
    )


def test_every_source_lands_in_exactly_one_cluster():
    sources = [fingerprint(f"s{i}", text=f"{DIFFERENT_STORY} {i}") for i in range(4)]

    clusters = cluster_sources(RUN, sources)

    placed = [source_id for cluster in clusters for source_id in cluster.source_ids]
    assert sorted(placed) == sorted(f.source_id for f in sources), (
        "corroboration is counted in clusters, so a source in none of them would "
        "be evidence that supports nothing"
    )
    assert len(placed) == len(set(placed)), "a source counted twice is corroboration invented"


def test_the_same_bytes_fetched_from_two_places_are_one_source():
    first = fingerprint("reuters", digest="same")
    second = fingerprint("syndicated", digest="same", minutes=5)

    clusters = cluster_sources(RUN, [first, second])

    assert len(clusters) == 1
    assert clusters[0].primary_source_id == first.source_id, "the copy fetched first represents it"
    assert clusters[0].duplicate_source_ids == (second.source_id,)
    assert clusters[0].reason == "exact_hash"


def test_one_page_fetched_twice_as_it_changed_is_still_one_source():
    first = fingerprint("blog", url="https://blog.test/post", digest="before")
    second = fingerprint(
        "blog-again", url="https://blog.test/post", digest="after", text=DIFFERENT_STORY, minutes=30
    )

    clusters = cluster_sources(RUN, [first, second])

    assert len(clusters) == 1, "the same canonical URL is the same page whatever it now says"
    assert clusters[0].reason == "canonical_url"


def test_a_syndicated_copy_is_recognised_without_matching_byte_for_byte():
    original = fingerprint("wire")
    # The same story with a different byline and one sentence added: a different
    # digest and a different URL, but the text a reader would recognise.
    copy = fingerprint(
        "reprint",
        digest="other",
        text=f"{WIRE_STORY} The report was filed by a staff correspondent.",
        minutes=10,
    )

    clusters = cluster_sources(RUN, [original, copy])

    assert len(clusters) == 1, "four reprints of one story are one source's word four times"
    assert clusters[0].reason == "near_duplicate", "a judgement is recorded as a judgement"
    assert clusters[0].primary_source_id == original.source_id


def test_two_articles_about_the_same_subject_stay_two_sources():
    reporting = fingerprint("newspaper")
    commentary = fingerprint(
        "analyst", digest="other", title="Inference pricing", text=DIFFERENT_STORY, minutes=1
    )

    clusters = cluster_sources(RUN, [reporting, commentary])

    assert len(clusters) == 2, (
        "collapsing two independent accounts is how corroboration is silently lost"
    )
    assert all(cluster.duplicate_source_ids == () for cluster in clusters)


def test_two_short_excerpts_are_not_duplicates_just_for_being_short():
    # Both below the shingle floor: a cookie banner is identical on every site
    # and says nothing about whether the articles beneath it are the same.
    banner = "Accept cookies to continue reading."
    first = fingerprint("a", text=banner)
    second = fingerprint("b", digest="other", text=banner, minutes=1)

    clusters = cluster_sources(RUN, [first, second])

    assert len(banner.split()) - 2 < MIN_SHINGLES, (
        "the banner is below the floor, which is the point"
    )
    assert len(clusters) == 2, "boilerplate is not evidence that two pages are the same page"


def test_a_cluster_held_together_by_a_judgement_is_reported_as_one():
    # Three sources: two share a digest (certain), the third only overlaps in
    # text (a judgement). The cluster is only as certain as its weakest link.
    first = fingerprint("a", digest="same")
    second = fingerprint("b", digest="same", minutes=1)
    third = fingerprint(
        "c",
        digest="other",
        text=f"{WIRE_STORY} Additional reporting by a correspondent.",
        minutes=2,
    )

    clusters = cluster_sources(RUN, [first, second, third])

    assert len(clusters) == 1
    assert clusters[0].reason == "near_duplicate"


def test_a_cluster_id_is_derived_so_re_clustering_does_not_renumber_it():
    sources = [fingerprint("a", digest="same"), fingerprint("b", digest="same", minutes=1)]

    first_pass = cluster_sources(RUN, sources)
    second_pass = cluster_sources(RUN, list(reversed(sources)))

    assert first_pass[0].cluster_id == second_pass[0].cluster_id
    assert first_pass[0].cluster_id == cluster_identity(RUN, first_pass[0].primary_source_id)
    assert cluster_identity(uuid.uuid4(), sources[0].source_id) != first_pass[0].cluster_id, (
        "clustering never spans two runs, so the run is part of the identity"
    )


def test_an_empty_run_clusters_to_nothing():
    assert cluster_sources(RUN, []) == []


def test_the_threshold_is_where_the_module_says_it_is():
    # Guards the constant itself: lowering it is a decision about how readily
    # the system collapses two sources, not a tuning detail.
    assert NEAR_DUPLICATE_RATIO >= 0.85
