# bot/cogs/roletracker.py
from __future__ import annotations
from datetime import datetime, timezone
import discord

from discord.ext import commands

async def _save_snapshot(bot, guild_id: int, user_id: int, roles: list[discord.Role]):
    # store every role except @everyone; we record managed too, but will skip re-adding those later automatically
    role_ids = ",".join(str(r.id) for r in roles if r and r.is_default() is False)
    await bot.db.execute(
        "INSERT OR REPLACE INTO role_snapshots(guild_id, user_id, roles, updated_at) VALUES(?, ?, ?, ?)",
        guild_id, user_id, role_ids, datetime.now(timezone.utc).isoformat()
    )

class RoleTracker(commands.Cog, name="RoleTracker"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.guild != after.guild:
            return
        if before.roles != after.roles:
            await _save_snapshot(self.bot, after.guild.id, after.id, [r for r in after.roles if not r.is_default()])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await _save_snapshot(self.bot, member.guild.id, member.id, [r for r in member.roles if not r.is_default()])

    @commands.Cog.listener()
    async def on_ready(self):
        # Best-effort warm snapshot for any members missing records
        for g in self.bot.guilds:
            for m in g.members:
                row = await self.bot.db.fetchrow("SELECT 1 FROM role_snapshots WHERE guild_id=? AND user_id=?", g.id, m.id)
                if not row:
                    await _save_snapshot(self.bot, g.id, m.id, [r for r in m.roles if not r.is_default()])

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleTracker(bot))
