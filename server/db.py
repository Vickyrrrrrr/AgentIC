import logging
import os
import time
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, JSON, String, create_engine, inspect, text, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agentic_jobs.db")
logger = logging.getLogger("agentic.db")

_IS_SQLITE = "sqlite" in DATABASE_URL

if _IS_SQLITE:
    # SQLite: single writer, WAL mode for concurrent reads without blocking writes
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        # Serialize all writes through a single connection to prevent
        # 'database is locked' errors from concurrent Celery + API writes.
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    # Enable WAL journal mode and busy timeout so readers never block writers
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # wait up to 5s before raising locked error
        cursor.execute("PRAGMA synchronous=NORMAL")  # faster writes, still crash-safe
        cursor.close()
else:
    # Postgres / MySQL: use a proper connection pool
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,       # recycle stale connections automatically
        pool_size=10,             # keep 10 warm connections ready
        max_overflow=20,          # allow up to 30 total under burst load
        pool_timeout=30,          # wait up to 30s for a free connection
        pool_recycle=1800,        # recycle connections every 30 min (avoids MySQL 8hr timeout)
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
_SCHEMA_READY = False


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=True)
    user_email = Column(String, index=True, nullable=True)
    design_name = Column(String, index=True)
    status = Column(String, default="pending")
    build_status = Column(String, default="pending")
    human_in_loop = Column(Boolean, default=False)
    waiting_approval = Column(Boolean, default=False)
    waiting_stage = Column(String, default="")

    request_data = Column(JSON, default=dict)
    events = Column(JSON, default=list)
    stages = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def is_schema_ready() -> bool:
    return _SCHEMA_READY


def _apply_runtime_migrations() -> None:
    """Backfill lightweight schema changes without a dedicated migration tool."""
    inspector = inspect(engine)
    if not inspector.has_table("jobs"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("jobs")}
    statements = []

    if "user_id" not in existing_columns:
        statements.append("ALTER TABLE jobs ADD COLUMN user_id VARCHAR")
    if "user_email" not in existing_columns:
        statements.append("ALTER TABLE jobs ADD COLUMN user_email VARCHAR")

    if not statements:
        return

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def ensure_database_ready(max_attempts: int = 1, retry_interval: float = 1.0) -> bool:
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return True

    last_error = None
    attempts = max(1, max_attempts)
    delay = max(0.0, retry_interval)

    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(bind=engine)
            _apply_runtime_migrations()
            _SCHEMA_READY = True
            if attempt > 1:
                logger.info("Database schema became ready on attempt %s/%s", attempt, attempts)
            return True
        except Exception as exc:
            last_error = exc
            logger.warning(
                "DB schema init attempt %s/%s failed: %s", attempt, attempts, exc
            )
            if attempt < attempts and delay:
                time.sleep(delay)

    logger.error("DB schema init failed after %s attempts: %s", attempts, last_error)
    return False


def get_db():
    if not ensure_database_ready():
        raise RuntimeError("Database is not ready")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
