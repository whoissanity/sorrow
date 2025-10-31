# bot/cogs/community.py
from __future__ import annotations

import json
from typing import Optional

import discord
from discord.ext import commands
from datetime import datetime, timezone

TABLE = """
CREATE TABLE IF NOT EXISTS community_config (
  guild_id INTEGER PRIMARY KEY,
  welcome_channel_id INTEGER,
  welcome_message TEXT,
  booster_bonus_role_id INTEGER
)
"""

def ISO(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

class Community(commands.Cog, name="Community"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.execute(TABLE)

    async def _config(self, gid: int) -> dict:
        row = await self.bot.db.fetchrow("SELECT * FROM community_config WHERE guild_id=?", gid)
        if not row:
            await self.bot.db.execute("INSERT INTO community_config(guild_id) VALUES(?)", gid)
            row = await self.bot.db.fetchrow("SELECT * FROM community_config WHERE guild_id=?", gid)
        return dict(row)

    # ------ Greeter ------
    @commands.group(name="greeter", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def greeter(self, ctx: commands.Context):
        c = await self._config(ctx.guild.id)
        ch = f"<#{c['welcome_channel_id']}>" if c.get("welcome_channel_id") else "unset"
        await ctx.reply(f"welcome: {ch}")

    @greeter.command(name="channel")
    @commands.has_permissions(manage_guild=True)
    async def greeter_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "INSERT INTO community_config(guild_id, welcome_channel_id) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id=excluded.welcome_channel_id",
            ctx.guild.id, channel.id
        )
        await ctx.reply("ok.")

    @greeter.command(name="message")
    @commands.has_permissions(manage_guild=True)
    async def greeter_message(self, ctx: commands.Context, *, text: str):
        await self.bot.db.execute(
            "INSERT INTO community_config(guild_id, welcome_message) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET welcome_message=excluded.welcome_message",
            ctx.guild.id, text[:1900]
        )
        await ctx.reply("ok.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        c = await self._config(member.guild.id)
        ch = member.guild.get_channel(c.get("welcome_channel_id") or 0)
        msg = c.get("welcome_message") or "welcome {mention}!"
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(msg.replace("{mention}", member.mention))
            except Exception:
                pass

    # ------ Booster perks ------
    @commands.group(name="boosterperks", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def boosterperks(self, ctx: commands.Context):
        c = await self._config(ctx.guild.id)
        rid = c.get("booster_bonus_role_id")
        await ctx.reply(f"booster bonus role: {(ctx.guild.get_role(rid).mention if rid and ctx.guild.get_role(rid) else 'unset')}")

    @boosterperks.command(name="role")
    @commands.has_permissions(manage_guild=True)
    async def boosterperks_role(self, ctx: commands.Context, role: discord.Role):
        await self.bot.db.execute(
            "INSERT INTO community_config(guild_id, booster_bonus_role_id) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET booster_bonus_role_id=excluded.booster_bonus_role_id",
            ctx.guild.id, role.id
        )
        await ctx.reply("ok.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since is None and after.premium_since is not None:
            c = await self._config(after.guild.id)
            rid = c.get("booster_bonus_role_id")
            if not rid:
                return
            role = after.guild.get_role(rid)
            if role and role < after.guild.me.top_role and role not in after.roles:
                try:
                    await after.add_roles(role, reason="booster perks")
                except Exception:
                    pass

async def setup(bot: commands.Bot):
    await bot.add_cog(Community(bot))
