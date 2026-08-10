from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PostgresAdvisoryRequestGate:
    """Serialize one site's requests across every worker process."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        site_id: str,
        *,
        cooldown_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        digest = hashlib.sha256(f"unin:site-request:{site_id}".encode()).digest()[:8]
        self.lock_id = int.from_bytes(digest, byteorder="big", signed=True)
        self.session_factory = session_factory
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.sleep = sleep

    @asynccontextmanager
    async def limit(self, operation: str) -> AsyncIterator[None]:
        del operation
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": self.lock_id},
                )
                try:
                    yield
                finally:
                    await self._cool_down()

    async def _cool_down(self) -> None:
        if self.cooldown_seconds <= 0:
            return
        cooldown: asyncio.Future[None] = asyncio.ensure_future(
            self.sleep(self.cooldown_seconds)
        )
        try:
            await asyncio.shield(cooldown)
        except asyncio.CancelledError:
            await cooldown
            raise
