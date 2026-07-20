"""Persistence: SQLAlchemy models, session management, repositories, asset store.

SQLite by default (zero-config, local-first); swap ``AETHERSEED_DATABASE_URL``
to a Postgres DSN for production. The ORM models here are the durable record of
every run, seed, page, asset, and failure so that any job is fully resumable and
auditable.
"""

from __future__ import annotations
