"""Core research schema.

The system of record from docs/TDD.md section 7.2: users and sessions, research
projects, runs and planner subtasks, sources, documents and chunks, claims,
evidence and contradictions, reports, sections and citations, the execution
trace, and evaluation and feedback rows.

Deliberately excludes the ``document_chunks.embedding`` column. pgvector is a
server-side prerequisite that a managed Postgres may need enabled separately,
so it gets its own revision: the relational schema can then be deployed and
verified independently, and a missing extension fails in one isolated place
with an actionable error rather than taking the whole schema with it.

Revision ID: 0001_core_schema
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_core_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions first: `citext` provides the case-insensitive email type and
    # `pgcrypto` the gen_random_uuid() default every primary key relies on.
    # Both are idempotent, so a re-run on a prepared database is a no-op.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("name", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column("role", sa.String(length=20), server_default=sa.text("'user'"), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_created_at"), "users", ["created_at"], unique=False)
    op.create_table(
        "research_projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_research_projects_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_projects")),
    )
    op.create_index(
        op.f("ix_research_projects_created_at"), "research_projects", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_research_projects_user_id"), "research_projects", ["user_id"], unique=False
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(op.f("ix_sessions_created_at"), "sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_sessions_user_id_expires_at", "sessions", ["user_id", "expires_at"], unique=False
    )
    op.create_table(
        "research_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("parent_run_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("depth", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "domains",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("date_range_start", sa.Date(), nullable=True),
        sa.Column("date_range_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "progress",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("langgraph_thread_id", sa.Text(), nullable=True),
        sa.Column("langgraph_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("iteration_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("contradiction_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("coverage_caveat", sa.Text(), nullable=True),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint(
            "mode IN ('quick', 'deep', 'conversational')",
            name=op.f("ck_research_runs_research_runs_mode"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'planning', 'researching', 'verifying', 'synthesizing', 'validating', 'paused', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_research_runs_research_runs_status"),
        ),
        sa.CheckConstraint(
            "depth BETWEEN 1 AND 5", name=op.f("ck_research_runs_ck_research_runs_depth")
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 1",
            name=op.f("ck_research_runs_ck_research_runs_progress_range"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["research_runs.id"],
            name=op.f("fk_research_runs_parent_run_id_research_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["research_projects.id"],
            name=op.f("fk_research_runs_project_id_research_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_research_runs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_runs")),
    )
    op.create_index(
        op.f("ix_research_runs_created_at"), "research_runs", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_research_runs_parent_run_id"), "research_runs", ["parent_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_runs_project_id"), "research_runs", ["project_id"], unique=False
    )
    op.create_index(op.f("ix_research_runs_status"), "research_runs", ["status"], unique=False)
    op.create_index(
        "ix_research_runs_status_created_at",
        "research_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_research_runs_user_id"), "research_runs", ["user_id"], unique=False)
    op.create_index(
        "ix_research_runs_user_id_created_at_id",
        "research_runs",
        ["user_id", sa.literal_column("created_at DESC"), "id"],
        unique=False,
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("benchmark_id", sa.String(length=120), nullable=False),
        sa.Column("dataset_version", sa.String(length=60), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('retrieval', 'generation', 'agent', 'infrastructure', 'full')",
            name=op.f("ck_evaluations_evaluations_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_evaluations_run_id_research_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
    )
    op.create_index(
        op.f("ix_evaluations_benchmark_id"), "evaluations", ["benchmark_id"], unique=False
    )
    op.create_index(
        "ix_evaluations_benchmark_id_created_at",
        "evaluations",
        ["benchmark_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_evaluations_created_at"), "evaluations", ["created_at"], unique=False)
    op.create_index(op.f("ix_evaluations_git_sha"), "evaluations", ["git_sha"], unique=False)
    op.create_index(op.f("ix_evaluations_run_id"), "evaluations", ["run_id"], unique=False)
    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "overall_confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("word_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_caveat", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'validated', 'published')", name=op.f("ck_reports_reports_status")
        ),
        sa.CheckConstraint(
            "overall_confidence BETWEEN 0 AND 1",
            name=op.f("ck_reports_ck_reports_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_reports_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
    )
    op.create_index(op.f("ix_reports_created_at"), "reports", ["created_at"], unique=False)
    op.create_index(op.f("ix_reports_run_id"), "reports", ["run_id"], unique=True)
    op.create_table(
        "research_tasks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rationale", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("iteration", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("source_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('high', 'medium', 'low')",
            name=op.f("ck_research_tasks_research_tasks_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'researching', 'done', 'insufficient')",
            name=op.f("ck_research_tasks_research_tasks_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_research_tasks_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_tasks")),
    )
    op.create_index(
        op.f("ix_research_tasks_created_at"), "research_tasks", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_research_tasks_run_id"), "research_tasks", ["run_id"], unique=False)
    op.create_index(
        "uq_research_tasks_run_id_external_id_iteration",
        "research_tasks",
        ["run_id", "external_id", "iteration"],
        unique=True,
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "credibility_score",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column(
            "credibility_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedup_cluster_id", sa.UUID(), nullable=True),
        sa.Column(
            "relevance_score",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column("task_external_id", sa.String(length=80), nullable=True),
        sa.Column("claim_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("excerpt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('web', 'sec', 'arxiv', 'github', 'upload')",
            name=op.f("ck_sources_sources_source_type"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_sources_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
    )
    op.create_index(op.f("ix_sources_canonical_url"), "sources", ["canonical_url"], unique=False)
    op.create_index(op.f("ix_sources_content_hash"), "sources", ["content_hash"], unique=False)
    op.create_index(op.f("ix_sources_created_at"), "sources", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_sources_dedup_cluster_id"), "sources", ["dedup_cluster_id"], unique=False
    )
    op.create_index(op.f("ix_sources_domain"), "sources", ["domain"], unique=False)
    op.create_index(op.f("ix_sources_run_id"), "sources", ["run_id"], unique=False)
    op.create_index(
        "ix_sources_run_id_content_hash", "sources", ["run_id", "content_hash"], unique=False
    )
    op.create_index(
        "ix_sources_run_id_created_at", "sources", ["run_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_sources_source_type"), "sources", ["source_type"], unique=False)
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("task_external_id", sa.String(length=80), nullable=True),
        sa.Column("agent_name", sa.String(length=30), nullable=False),
        sa.Column("iteration", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "agent_name IN ('planner', 'researcher', 'evidence_extractor', 'claim_normalizer', 'verifier', 'critic', 'synthesizer', 'citation_validator')",
            name=op.f("ck_agent_runs_agent_runs_agent_name"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'error')", name=op.f("ck_agent_runs_agent_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_agent_runs_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_tasks.id"],
            name=op.f("fk_agent_runs_task_id_research_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_created_at"), "agent_runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_agent_runs_run_id"), "agent_runs", ["run_id"], unique=False)
    op.create_index(
        "ix_agent_runs_run_id_started_at", "agent_runs", ["run_id", "started_at"], unique=False
    )
    op.create_index(op.f("ix_agent_runs_task_id"), "agent_runs", ["task_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_trace_id"), "agent_runs", ["trace_id"], unique=False)
    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("task_external_id", sa.String(length=80), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("predicate", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("object_value", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("claim_type", sa.String(length=20), nullable=False),
        sa.Column("normalized_key", sa.String(length=255), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("corroboration_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "claim_type IN ('quantitative', 'qualitative', 'event')",
            name=op.f("ck_claims_claims_claim_type"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'verified', 'refuted', 'contested')",
            name=op.f("ck_claims_claims_status"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_claims_ck_claims_confidence_range")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_claims_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_tasks.id"],
            name=op.f("fk_claims_task_id_research_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
    )
    op.create_index(op.f("ix_claims_created_at"), "claims", ["created_at"], unique=False)
    op.create_index(op.f("ix_claims_normalized_key"), "claims", ["normalized_key"], unique=False)
    op.create_index(op.f("ix_claims_run_id"), "claims", ["run_id"], unique=False)
    op.create_index(
        "ix_claims_run_id_normalized_key", "claims", ["run_id", "normalized_key"], unique=False
    )
    op.create_index("ix_claims_run_id_status", "claims", ["run_id", "status"], unique=False)
    op.create_index(op.f("ix_claims_status"), "claims", ["status"], unique=False)
    op.create_index(op.f("ix_claims_task_id"), "claims", ["task_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column(
            "mime_type",
            sa.String(length=120),
            server_default=sa.text("'text/html'"),
            nullable=False,
        ),
        sa.Column("raw_size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column(
            "extraction_method",
            sa.String(length=50),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_documents_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_documents_content_hash")),
    )
    op.create_index(op.f("ix_documents_created_at"), "documents", ["created_at"], unique=False)
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_table(
        "feedback",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("helpful", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("comment", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('accuracy', 'completeness', 'citations', 'readability', 'other')",
            name=op.f("ck_feedback_feedback_category"),
        ),
        sa.CheckConstraint(
            "rating BETWEEN 1 AND 5", name=op.f("ck_feedback_ck_feedback_rating_range")
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_feedback_report_id_reports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_feedback_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_feedback_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
    )
    op.create_index(op.f("ix_feedback_created_at"), "feedback", ["created_at"], unique=False)
    op.create_index(op.f("ix_feedback_report_id"), "feedback", ["report_id"], unique=False)
    op.create_index(op.f("ix_feedback_run_id"), "feedback", ["run_id"], unique=False)
    op.create_index(op.f("ix_feedback_user_id"), "feedback", ["user_id"], unique=False)
    op.create_index("uq_feedback_run_id_user_id", "feedback", ["run_id", "user_id"], unique=True)
    op.create_table(
        "report_sections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('executive_summary', 'key_findings', 'detailed_analysis', 'competitive_landscape', 'evidence', 'contradictions', 'confidence_assessment', 'recommendations', 'references')",
            name=op.f("ck_report_sections_report_sections_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_sections_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_sections")),
    )
    op.create_index(
        op.f("ix_report_sections_created_at"), "report_sections", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_report_sections_report_id"), "report_sections", ["report_id"], unique=False
    )
    op.create_index(
        "uq_report_sections_report_id_ordinal",
        "report_sections",
        ["report_id", "ordinal"],
        unique=True,
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("report_section_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_citations_ck_citations_ordinal_positive")),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_citations_claim_id_claims"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_section_id"],
            ["report_sections.id"],
            name=op.f("fk_citations_report_section_id_report_sections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_citations_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citations")),
    )
    op.create_index(op.f("ix_citations_claim_id"), "citations", ["claim_id"], unique=False)
    op.create_index(op.f("ix_citations_created_at"), "citations", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_citations_report_section_id"), "citations", ["report_section_id"], unique=False
    )
    op.create_index(op.f("ix_citations_source_id"), "citations", ["source_id"], unique=False)
    op.create_table(
        "contradictions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("normalized_key", sa.String(length=255), nullable=False),
        sa.Column("claim_a_id", sa.UUID(), nullable=False),
        sa.Column("claim_b_id", sa.UUID(), nullable=False),
        sa.Column("value_a", sa.Text(), nullable=False),
        sa.Column("value_b", sa.Text(), nullable=False),
        sa.Column("source_a_id", sa.UUID(), nullable=False),
        sa.Column("source_b_id", sa.UUID(), nullable=False),
        sa.Column("likely_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("resolution", sa.String(length=30), nullable=False),
        sa.Column("resolved_by", sa.String(length=50), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "resolution IN ('unresolved', 'resolved_a', 'resolved_b', 'both_valid_in_context')",
            name=op.f("ck_contradictions_contradictions_resolution"),
        ),
        sa.CheckConstraint(
            "claim_a_id <> claim_b_id",
            name=op.f("ck_contradictions_ck_contradictions_distinct_claims"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_a_id"],
            ["claims.id"],
            name=op.f("fk_contradictions_claim_a_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_b_id"],
            ["claims.id"],
            name=op.f("fk_contradictions_claim_b_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_contradictions_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_a_id"],
            ["sources.id"],
            name=op.f("fk_contradictions_source_a_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_b_id"],
            ["sources.id"],
            name=op.f("fk_contradictions_source_b_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contradictions")),
    )
    op.create_index(
        op.f("ix_contradictions_claim_a_id"), "contradictions", ["claim_a_id"], unique=False
    )
    op.create_index(
        op.f("ix_contradictions_claim_b_id"), "contradictions", ["claim_b_id"], unique=False
    )
    op.create_index(
        op.f("ix_contradictions_created_at"), "contradictions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_contradictions_normalized_key"), "contradictions", ["normalized_key"], unique=False
    )
    op.create_index(op.f("ix_contradictions_run_id"), "contradictions", ["run_id"], unique=False)
    op.create_index(
        "ix_contradictions_run_id_resolution",
        "contradictions",
        ["run_id", "resolution"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contradictions_source_a_id"), "contradictions", ["source_a_id"], unique=False
    )
    op.create_index(
        op.f("ix_contradictions_source_b_id"), "contradictions", ["source_b_id"], unique=False
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
    )
    op.create_index(
        op.f("ix_document_chunks_created_at"), "document_chunks", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"], unique=False
    )
    op.create_index(
        "ix_document_chunks_metadata",
        "document_chunks",
        ["metadata"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_document_chunks_tsv", "document_chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "uq_document_chunks_document_id_chunk_index",
        "document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("span_text", sa.Text(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("stance", sa.String(length=10), nullable=False),
        sa.Column("extractor_agent", sa.String(length=50), nullable=False),
        sa.Column("extractor_model", sa.String(length=120), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("0.50"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stance IN ('supports', 'refutes', 'neutral')", name=op.f("ck_evidence_evidence_stance")
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_evidence_ck_evidence_confidence_range")
        ),
        sa.CheckConstraint(
            "span_end > span_start", name=op.f("ck_evidence_ck_evidence_span_order")
        ),
        sa.CheckConstraint(
            "span_start >= 0", name=op.f("ck_evidence_ck_evidence_span_start_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_evidence_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_evidence_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_evidence_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_claim_id"), "evidence", ["claim_id"], unique=False)
    op.create_index("ix_evidence_claim_id_stance", "evidence", ["claim_id", "stance"], unique=False)
    op.create_index(op.f("ix_evidence_created_at"), "evidence", ["created_at"], unique=False)
    op.create_index(op.f("ix_evidence_document_id"), "evidence", ["document_id"], unique=False)
    op.create_index(op.f("ix_evidence_source_id"), "evidence", ["source_id"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_run_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column(
            "prompt_version", sa.String(length=80), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("temperature", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('openai', 'anthropic', 'gemini', 'ollama')",
            name=op.f("ck_llm_calls_llm_calls_provider"),
        ),
        sa.CheckConstraint(
            "role IN ('planner', 'researcher', 'evidence_extractor', 'claim_normalizer', 'verifier', 'critic', 'synthesizer', 'citation_validator')",
            name=op.f("ck_llm_calls_llm_calls_role"),
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'fallback')", name=op.f("ck_llm_calls_llm_calls_status")
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_llm_calls_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_llm_calls_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_agent_run_id"), "llm_calls", ["agent_run_id"], unique=False)
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"], unique=False)
    op.create_index(
        "ix_llm_calls_provider_model_created_at",
        "llm_calls",
        ["provider", "model", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_llm_calls_request_hash"), "llm_calls", ["request_hash"], unique=False)
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_index(
        "ix_llm_calls_run_id_created_at", "llm_calls", ["run_id", "created_at"], unique=False
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_run_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(length=30), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("retries", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'rate_limited', 'timeout')",
            name=op.f("ck_tool_calls_tool_calls_status"),
        ),
        sa.CheckConstraint(
            "tool_name IN ('search', 'fetch', 'parse', 'retrieve', 'sec_api', 'arxiv_api', 'github_api')",
            name=op.f("ck_tool_calls_tool_calls_tool_name"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    op.create_index(
        op.f("ix_tool_calls_agent_run_id"), "tool_calls", ["agent_run_id"], unique=False
    )
    op.create_index(op.f("ix_tool_calls_created_at"), "tool_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_tool_calls_tool_name"), "tool_calls", ["tool_name"], unique=False)
    op.create_index(
        "ix_tool_calls_tool_name_status", "tool_calls", ["tool_name", "status"], unique=False
    )


def downgrade() -> None:
    """Drops every table, in dependency order.

    The extensions are deliberately left in place: they may predate this
    schema or be used by something else in the database, and dropping a shared
    extension on a rollback is a far worse failure than leaving one behind.
    """
    op.drop_index("ix_tool_calls_tool_name_status", table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_tool_name"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_created_at"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_agent_run_id"), table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_llm_calls_run_id_created_at", table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_request_hash"), table_name="llm_calls")
    op.drop_index("ix_llm_calls_provider_model_created_at", table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_agent_run_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_evidence_source_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_document_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_created_at"), table_name="evidence")
    op.drop_index("ix_evidence_claim_id_stance", table_name="evidence")
    op.drop_index(op.f("ix_evidence_claim_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("uq_document_chunks_document_id_chunk_index", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tsv", table_name="document_chunks", postgresql_using="gin")
    op.drop_index(
        "ix_document_chunks_metadata", table_name="document_chunks", postgresql_using="gin"
    )
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_created_at"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_contradictions_source_b_id"), table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_source_a_id"), table_name="contradictions")
    op.drop_index("ix_contradictions_run_id_resolution", table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_run_id"), table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_normalized_key"), table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_created_at"), table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_claim_b_id"), table_name="contradictions")
    op.drop_index(op.f("ix_contradictions_claim_a_id"), table_name="contradictions")
    op.drop_table("contradictions")
    op.drop_index(op.f("ix_citations_source_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_report_section_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_created_at"), table_name="citations")
    op.drop_index(op.f("ix_citations_claim_id"), table_name="citations")
    op.drop_table("citations")
    op.drop_index("uq_report_sections_report_id_ordinal", table_name="report_sections")
    op.drop_index(op.f("ix_report_sections_report_id"), table_name="report_sections")
    op.drop_index(op.f("ix_report_sections_created_at"), table_name="report_sections")
    op.drop_table("report_sections")
    op.drop_index("uq_feedback_run_id_user_id", table_name="feedback")
    op.drop_index(op.f("ix_feedback_user_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_run_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_report_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_created_at"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_created_at"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_claims_task_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_status"), table_name="claims")
    op.drop_index("ix_claims_run_id_status", table_name="claims")
    op.drop_index("ix_claims_run_id_normalized_key", table_name="claims")
    op.drop_index(op.f("ix_claims_run_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_normalized_key"), table_name="claims")
    op.drop_index(op.f("ix_claims_created_at"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_agent_runs_trace_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_task_id"), table_name="agent_runs")
    op.drop_index("ix_agent_runs_run_id_started_at", table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_run_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_created_at"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_sources_source_type"), table_name="sources")
    op.drop_index("ix_sources_run_id_created_at", table_name="sources")
    op.drop_index("ix_sources_run_id_content_hash", table_name="sources")
    op.drop_index(op.f("ix_sources_run_id"), table_name="sources")
    op.drop_index(op.f("ix_sources_domain"), table_name="sources")
    op.drop_index(op.f("ix_sources_dedup_cluster_id"), table_name="sources")
    op.drop_index(op.f("ix_sources_created_at"), table_name="sources")
    op.drop_index(op.f("ix_sources_content_hash"), table_name="sources")
    op.drop_index(op.f("ix_sources_canonical_url"), table_name="sources")
    op.drop_table("sources")
    op.drop_index("uq_research_tasks_run_id_external_id_iteration", table_name="research_tasks")
    op.drop_index(op.f("ix_research_tasks_run_id"), table_name="research_tasks")
    op.drop_index(op.f("ix_research_tasks_created_at"), table_name="research_tasks")
    op.drop_table("research_tasks")
    op.drop_index(op.f("ix_reports_run_id"), table_name="reports")
    op.drop_index(op.f("ix_reports_created_at"), table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_evaluations_run_id"), table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_git_sha"), table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_created_at"), table_name="evaluations")
    op.drop_index("ix_evaluations_benchmark_id_created_at", table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_benchmark_id"), table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_index("ix_research_runs_user_id_created_at_id", table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_user_id"), table_name="research_runs")
    op.drop_index("ix_research_runs_status_created_at", table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_status"), table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_project_id"), table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_parent_run_id"), table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_created_at"), table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_index("ix_sessions_user_id_expires_at", table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_created_at"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_research_projects_user_id"), table_name="research_projects")
    op.drop_index(op.f("ix_research_projects_created_at"), table_name="research_projects")
    op.drop_table("research_projects")
    op.drop_index(op.f("ix_users_created_at"), table_name="users")
    op.drop_table("users")
