"""
Database Configuration for Seedarr v2.0

This module provides database connection and session management.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from app.config import Config

# Database configuration - use Config for consistency
DATABASE_URL = Config.DATABASE_URL

# Create database directory if it doesn't exist
db_dir = os.path.dirname(DATABASE_URL.replace('sqlite:///', ''))
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

# SQLAlchemy engine and session
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith('sqlite') else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """
    FastAPI dependency for database sessions.

    Yields:
        SQLAlchemy session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
