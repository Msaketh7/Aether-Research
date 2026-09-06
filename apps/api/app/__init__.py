"""Aether Research backend.

One codebase, two process types (ADR 0001):
``app.main:app`` serves HTTP, ``app.workers.runner`` executes research runs.
"""
