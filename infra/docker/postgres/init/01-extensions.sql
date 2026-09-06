-- Extensions Aether relies on. Alembic owns the schema itself (Phase 3);
-- extensions are created here because they need superuser at database bootstrap.
CREATE EXTENSION IF NOT EXISTS "vector";   -- pgvector: embedding storage + HNSW
CREATE EXTENSION IF NOT EXISTS "citext";   -- case-insensitive email addresses
CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- gen_random_uuid()
