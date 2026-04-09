import logging
import os
import time
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, JSON, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Fallback to local SQLite if running outside of Docker
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agentic_jobs.db")
logger = logging.getLogger("agentic.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
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
    status = Column(String, default="pending")  # pending, running, completed, failed
    build_status = Column(String, default="pending")
    human_in_loop = Column(Boolean, default=False)
    waiting_approval = Column(Boolean, default=False)
    waiting_stage = Column(String, default="")
    
    # Store complex data as JSON fields
    request_data = Column(JSON, default=dict)
    events = Column(JSON, default=list)  # Eventually move to a separate table for massive scale
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
    """Create the schema on demand without crashing module import time."""
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
                "Database schema initialization attempt %s/%s failed: %s",
                attempt,
                attempts,
                exc,
            )
            if attempt < attempts and delay:
                time.sleep(delay)

    logger.error("Database schema initialization failed after %s attempts: %s", attempts, last_error)
    return False

def get_db():
    if not ensure_database_ready():
        raise RuntimeError("Database is not ready")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
