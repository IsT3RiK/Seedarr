"""
SQLAlchemy Base Configuration for Seedarr v2.0

This module provides the declarative base class for all ORM models.
"""

from sqlalchemy.ext.declarative import declarative_base

# Create the declarative base class for all models
Base = declarative_base()
