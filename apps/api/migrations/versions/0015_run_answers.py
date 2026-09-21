"""The direct answer: its own table, and the vocabulary that carries it.

A run now writes a plain-prose answer to the question before it writes the
report, and streams it as it is written. Three schema changes follow.

**`run_answers` is new.** Its own table rather than a column on `reports`,
because the answer is produced *before* the report and is what the reader came
for: hanging it off the report would mean a run that answered the question and
then died at synthesis had nothing to show. The id is derived from the run, so
re-projecting a resumed run rewrites the answer rather than adding a second one.

**Three event types.** `answer_started`, `answer_delta` and `answer_completed`
are how the answer reaches a browser. A database created from scratch already
permits them - 0010 builds its check from the live enum, so it rewrites itself
as the vocabulary grows - and this is what widens the constraint on one that
already exists.

They They are ordinary `research_events` rows,
which is what makes a reconnect replay the answer exactly rather than showing
the reader a gap - and it is why the pieces are phrase-sized rather than
per-token, since each one is an insert.

**One new agent role.** `answerer` joins the vocabulary that `agent_runs` and
`llm_calls` are constrained to, so the ledger can attribute its calls. A check
constraint rather than a Postgres ENUM is exactly why this is an ordinary
`ALTER TABLE` (see `app/db/models/research.py::enum_check`).

The downgrade drops the table and narrows the three constraints again. It
deletes the answer rows and any `answer_*` events first: a narrower constraint
cannot be validated against rows that violate it, and a downgrade that fails
halfway is worse than one that says what it removed.

Revision ID: 0015_run_answers
Revises: 0014_federated_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.enums import AgentName, ResearchEventType

revision: str = "0015_run_answers"
down_revision: str | None = "0014_federated_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The vocabularies as they were before this migration, so the downgrade
#: restores exactly what it found rather than whatever the enums say today.
_EVENT_TYPES_BEFORE: tuple[str, ...] = (
    "research_started",
    "planner_started",
    "planner_completed",
    "subtask_started",
    "search_started",
    "source_found",
    "source_processed",
    "sources_progress",
    "claim_extracted",
    "evidence_progress",
    "verification_started",
    "contradiction_found",
    "critic_started",
    "additional_research_requested",
    "iteration_started",
    "synthesis_started",
    "citation_check",
    "report_completed",
    "research_failed",
    "research_cancelled",
)
_AGENT_NAMES_BEFORE: tuple[str, ...] = (
    "planner",
    "researcher",
    "evidence_extractor",
    "claim_normalizer",
    "verifier",
    "critic",
    "synthesizer",
    "citation_validator",
)

_ANSWER_EVENT_TYPES = ("answer_started", "answer_delta", "answer_completed")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _replace_check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    """Widen or narrow one enum check, in place.

    ``op.f`` on both halves, and it is load-bearing: without it Alembic applies
    the metadata's naming convention to a name that already carries its prefix
    and looks for ``ck_agent_runs_ck_agent_runs_agent_runs_agent_name``. The
    0001 migration wrapped every constraint name the same way, which is why the
    names in the database are the ones written here.
    """
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in_list(column, values))


def upgrade() -> None:
    op.create_table(
        "run_answers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("word_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("citation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("word_count >= 0", name=op.f("ck_run_answers_run_answers_word_count")),
        sa.CheckConstraint(
            "citation_count >= 0", name=op.f("ck_run_answers_run_answers_citation_count")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_run_answers_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_answers")),
    )
    # A unique *index* rather than a unique constraint, because that is what
    # `fk_uuid(..., unique=True)` declares on the model - `reports.run_id` is
    # the same shape. The two are interchangeable to Postgres and not to
    # Alembic's autogenerate, which is what `test_the_migrations_and_the_models
    # _agree` compares.
    op.create_index(op.f("ix_run_answers_run_id"), "run_answers", ["run_id"], unique=True)
    op.create_index(op.f("ix_run_answers_created_at"), "run_answers", ["created_at"])

    _replace_check(
        "research_events",
        # Doubled on purpose: 0010 created this table with an already-prefixed
        # name and Alembic applied the metadata's `ck_` convention on top of it,
        # so this is the name the column actually carries. Read it out of
        # `pg_constraint` before changing it, never off the model.
        "ck_research_events_ck_research_events_research_events_type",
        "type",
        [member.value for member in ResearchEventType],
    )
    _replace_check(
        "agent_runs",
        "ck_agent_runs_agent_runs_agent_name",
        "agent_name",
        [member.value for member in AgentName],
    )
    _replace_check(
        "llm_calls",
        "ck_llm_calls_llm_calls_role",
        "role",
        [member.value for member in AgentName],
    )


def downgrade() -> None:
    # Rows first. A constraint that no longer permits `answerer` cannot be added
    # to a table that holds one, and the failure would land halfway through.
    op.execute(
        sa.text("DELETE FROM research_events WHERE type IN :types").bindparams(
            sa.bindparam("types", value=_ANSWER_EVENT_TYPES, expanding=True)
        )
    )
    op.execute(sa.text("DELETE FROM llm_calls WHERE role = 'answerer'"))
    op.execute(sa.text("DELETE FROM agent_runs WHERE agent_name = 'answerer'"))

    _replace_check("llm_calls", "ck_llm_calls_llm_calls_role", "role", _AGENT_NAMES_BEFORE)
    _replace_check(
        "agent_runs", "ck_agent_runs_agent_runs_agent_name", "agent_name", _AGENT_NAMES_BEFORE
    )
    _replace_check(
        "research_events",
        "ck_research_events_ck_research_events_research_events_type",
        "type",
        _EVENT_TYPES_BEFORE,
    )
    op.drop_index(op.f("ix_run_answers_created_at"), table_name="run_answers")
    op.drop_index(op.f("ix_run_answers_run_id"), table_name="run_answers")
    op.drop_table("run_answers")
