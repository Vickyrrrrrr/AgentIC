import os
import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

# Fallback to local SQLite if running outside of Docker
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agentic_jobs.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True)
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

# Create all tables in the engine
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
