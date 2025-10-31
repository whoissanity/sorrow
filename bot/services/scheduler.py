from __future__ import annotations
import logging
from datetime import datetime, timezone
import discord
from discord.ext import tasks, commands

log = logging.getLogger(__name__)

class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tempban_loop.start()

    def cog_unload(self):
        self.tempban_loop.cancel()

    @tasks.loop(seconds=30)
    async def tempban_loop(self):
        rows = await self.bot.db.fetchall(
            "SELECT guild_id, user_id, unban_at, reason FROM temp_bans WHERE unban_at <= ?",
            datetime.now(timezone.utc).isoformat()
        )
        for row in rows:
            guild = self.bot.get_guild(int(row['guild_id']))
            if not guild:
                continue
            try:
                await guild.unban(discord.Object(id=int(row['user_id'])), reason="Tempban expired")
            except discord.NotFound:
                pass
            except Exception:
                pass
            finally:
                await self.bot.db.execute(
                    "DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
                    guild.id, row['user_id']
                )

    @tempban_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
