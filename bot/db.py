from __future__ import annotations
import aiosqlite
from typing import Optional

class Database:
    def __init__(self, path: str = "bot.db"):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "DB not connected"
        return self._db

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.commit()

    async def setup(self) -> None:
        with open("data/schema.sql", "r", encoding="utf-8") as f:
            await self.db.executescript(f.read())
        await self.db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def fetchrow(self, query: str, *args):
        self.db.row_factory = aiosqlite.Row
        async with self.db.execute(query, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, *args):
        self.db.row_factory = aiosqlite.Row
        async with self.db.execute(query, args) as cur:
            return await cur.fetchall()

    async def execute(self, query: str, *args) -> None:
        await self.db.execute(query, args)
        await self.db.commit()

    # prefix helpers
    async def get_prefix(self, guild_id: int) -> str:
        row = await self.fetchrow("SELECT prefix FROM guild_config WHERE guild_id = ?", guild_id)
        return row["prefix"] if row else ","

    async def set_prefix(self, guild_id: int, prefix: str) -> None:
        await self.execute(
            "INSERT INTO guild_config(guild_id, prefix) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET prefix=excluded.prefix",
            guild_id, prefix
        )
