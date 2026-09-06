"""Benchmark results and user feedback.

An ``evaluations`` row records what was measured, against which dataset version
and which commit, with the thresholds in force at the time. Storing all three
is what makes a metric change attributable: to the code, to the data, or to a
moved goalpost.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EvaluationKind, FeedbackCategory
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check


class EvaluationRow(Base, TimestampMixin):
    """One test-case result from a benchmark run."""

    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Null for benchmark rows that did not execute a full research run.
    run_id: Mapped[uuid.UUID | None] = fk_uuid(ForeignKey("research_runs.id", ondelete="SET NULL"))
    benchmark_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    dataset_version: Mapped[str] = mapped_column(String(60), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Measured values only. A metric that was not measured is absent, never
    #: stored as zero - the two mean opposite things (docs/evaluation.md).
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: The gate values in force for this run, so a later threshold change
    #: cannot retroactively turn a pass into a fail.
    thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        enum_check("kind", EvaluationKind, "evaluations_kind"),
        # The history chart: results for one case over time.
        Index("ix_evaluations_benchmark_id_created_at", "benchmark_id", "created_at"),
    )


class FeedbackRow(Base, TimestampMixin):
    """A user's rating of a report."""

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[uuid.UUID | None] = fk_uuid(ForeignKey("reports.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    __table_args__ = (
        enum_check("category", FeedbackCategory, "feedback_category"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_feedback_rating_range"),
        # One rating per user per run; a change is an update, not a new row.
        Index("uq_feedback_run_id_user_id", "run_id", "user_id", unique=True),
    )
