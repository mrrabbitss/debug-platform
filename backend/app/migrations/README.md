# Database migrations

Alembic owns all persistent schema upgrades. Run `alembic upgrade head` from
the `backend` directory for a manual upgrade; normal application startup runs
the same upgrade automatically before opening a database session.
