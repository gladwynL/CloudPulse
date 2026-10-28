"""Shared FastAPI dependencies."""

import os
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db, get_session_factory
from app.monitoring.executor import Checker
from app.monitoring.security import TargetPolicy
from app.monitoring.store import CheckStore, SqlCheckStore

DbSession = Annotated[Session, Depends(get_db)]


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_policy(request: Request) -> TargetPolicy:
    return request.app.state.policy


def get_checker(request: Request) -> Checker:
    return request.app.state.checker


def get_check_store(settings: "AppSettings") -> CheckStore:
    return SqlCheckStore(
        get_session_factory(),
        owner=f"api:{os.getpid()}",
        lease_grace_seconds=settings.monitoring_lease_grace_seconds,
    )


AppSettings = Annotated[Settings, Depends(get_app_settings)]
PolicyDep = Annotated[TargetPolicy, Depends(get_policy)]
CheckerDep = Annotated[Checker, Depends(get_checker)]
CheckStoreDep = Annotated[CheckStore, Depends(get_check_store)]
