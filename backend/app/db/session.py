"""Connexion à la base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base


def _build_engine() -> Engine:
    url = settings.database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # SQLite est un confort de développement, pas une cible de production.
        db_path = url.split("///")[-1]
        kwargs["connect_args"] = {"check_same_thread": False}

        if db_path == ":memory:":
            # Une base SQLite en mémoire est **propre à chaque connexion** : avec
            # un pool classique, chaque connexion ouvrirait une base vide et les
            # tables créées au démarrage seraient invisibles ailleurs.
            # StaticPool force une connexion unique et partagée.
            kwargs["poolclass"] = StaticPool
        elif db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):  # pragma: no cover - branchement moteur
            # Sans cela SQLite ignore les clés étrangères et laisse passer des
            # incohérences que PostgreSQL refuserait.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session transactionnelle : commit si tout va bien, rollback sinon."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
