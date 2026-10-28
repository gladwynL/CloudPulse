"""Async façade over the lease repository.

The scheduler and the manual-check endpoint talk to ``CheckStore``; unit tests substitute
an in-memory fake, integration tests use ``SqlCheckStore`` against PostgreSQL.

Database calls are synchronous (psycopg + SQLAlchemy sessions) and short, so each runs in a
worker thread with its own session. No session or connection is held while an HTTP check
is in flight, which keeps the connection pool small regardless of check concurrency.
"""

import asyncio
import uuid
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.monitoring.types import CheckResult, CheckSource, ClaimedCheck, ClaimOutcome
from app.repositories import resources as repo


class CheckStore(Protocol):
    async def claim_due(self, limit: int) -> list[ClaimedCheck]: ...

    async def claim(self, resource_id: uuid.UUID) -> ClaimedCheck | ClaimOutcome: ...

    async def complete(
        self, claim: ClaimedCheck, result: CheckResult, source: CheckSource
    ) -> bool: ...

    async def release(self, claim: ClaimedCheck) -> None: ...


class SqlCheckStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        owner: str,
        lease_grace_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._owner = owner
        self._grace = lease_grace_seconds

    async def claim_due(self, limit: int) -> list[ClaimedCheck]:
        return await asyncio.to_thread(self._claim_due, limit)

    async def claim(self, resource_id: uuid.UUID) -> ClaimedCheck | ClaimOutcome:
        return await asyncio.to_thread(self._claim, resource_id)

    async def complete(self, claim: ClaimedCheck, result: CheckResult, source: CheckSource) -> bool:
        return await asyncio.to_thread(self._complete, claim, result, source)

    async def release(self, claim: ClaimedCheck) -> None:
        await asyncio.to_thread(self._release, claim)

    # --- synchronous bodies (run in threads) -----------------------------------------

    def _claim_due(self, limit: int) -> list[ClaimedCheck]:
        with self._session_factory() as session:
            claims = repo.claim_due(
                session, limit=limit, owner=self._owner, grace_seconds=self._grace
            )
            session.commit()
            return claims

    def _claim(self, resource_id: uuid.UUID) -> ClaimedCheck | ClaimOutcome:
        with self._session_factory() as session:
            outcome = repo.claim_by_id(
                session, resource_id, owner=self._owner, grace_seconds=self._grace
            )
            session.commit()
            return outcome

    def _complete(self, claim: ClaimedCheck, result: CheckResult, source: CheckSource) -> bool:
        # One transaction: latest-state update + history insert commit together or not at all.
        # An exception skips commit and the session closes with a rollback.
        with self._session_factory() as session:
            stored = repo.complete(session, claim, result, source)
            session.commit()
            return stored

    def _release(self, claim: ClaimedCheck) -> None:
        with self._session_factory() as session:
            repo.release(session, claim)
            session.commit()
