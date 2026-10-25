"""Dependency checks used by the readiness probe."""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def check_database(session: Session) -> bool:
    """Return True if a trivial query succeeds against the database."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Log the exception type only: driver messages can include connection details.
        logger.warning("database readiness check failed", extra={"error": type(exc).__name__})
        return False
    return True
