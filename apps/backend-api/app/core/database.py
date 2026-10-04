import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase, with_loader_criteria

from app.core.config import settings

# Connection-pool sizing. The Render Postgres instance caps TOTAL connections at
# ~100, and EVERY process (web + each Celery worker child + beat) keeps its own
# pool — so a big pool × many worker children exhausts the cap and triggers
# "FATAL: remaining connection slots…" 500s. Keep per-process pools small and
# tune per service via env: a tiny pool on the worker/beat services
# (DB_POOL_SIZE=2, DB_MAX_OVERFLOW=3) and a larger one on the web service.
_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "10"))  # fail fast, don't hang 30s

# pool_pre_ping: test a pooled connection with a lightweight ping before use and
# transparently replace it if dead. Managed Postgres / proxies (Render) drop idle
# connections, so without this a stale connection causes an intermittent 500 that
# "works on retry". pool_recycle proactively retires connections before the
# server's idle timeout.
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_timeout=_POOL_TIMEOUT,
    pool_pre_ping=True,
    pool_recycle=280,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# Option that lets a query see trashed deals (the Trash list and restore need it):
#   db.query(Deal).execution_options(include_trashed=True)
INCLUDE_TRASHED = "include_trashed"


@event.listens_for(SessionLocal, "do_orm_execute")
def _hide_trashed_deals(state):
    """A deal in Trash is invisible to EVERY select, on every session.

    One filter here instead of a `trashed_at IS NULL` on each of the dozens of
    Deal queries (commissions, weekly tiers, leaderboards, dashboards, coaching):
    a trashed sale stops counting everywhere at once, and a query written later
    cannot forget the rule. Only a query that opts in with INCLUDE_TRASHED sees
    trashed rows."""
    if not state.is_select or state.execution_options.get(INCLUDE_TRASHED):
        return
    from app.models.compliance import Deal   # late import: models import this module
    state.statement = state.statement.options(
        with_loader_criteria(Deal, lambda cls: cls.trashed_at.is_(None), include_aliases=True)
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
