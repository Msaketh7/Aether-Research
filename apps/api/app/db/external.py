"""Tables in this database that the ORM does not model.

LangGraph's checkpointer reads and writes its own tables (ADR 0014). Alembic
creates them, so there is still exactly one migration history - but no
SQLAlchemy model describes them, because nothing in this codebase queries them
directly and a model would be a second, unenforced copy of another project's
schema.

Autogenerate compares the database with ``Base.metadata`` and proposes dropping
every table it does not recognise. The Alembic environment and the schema-drift
test both consult this set, so the only tables excused from that comparison are
the ones named here - and a test checks that the names match what the library
actually creates.
"""

from __future__ import annotations

#: Created by migration 0005 from LangGraph's own DDL.
CHECKPOINT_TABLES = frozenset(
    {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
)

EXTERNALLY_OWNED_TABLES = CHECKPOINT_TABLES
