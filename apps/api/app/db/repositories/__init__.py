"""Postgres-backed repositories.

Each implements a protocol declared by the module that owns the domain, so the
service layer depends on the interface and never on SQLAlchemy.
"""
