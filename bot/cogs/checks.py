# bot/utils/checks.py
from __future__ import annotations
from typing import Callable
from discord.ext import commands
import discord

# --- Owner/Admin check --------------------------------------------------------
def is_guild_owner_or_admin():
    async def predicate(ctx: commands.Context):
        if not ctx.guild:
            return False
        return ctx.author == ctx.guild.owner or ctx.author.guild_permissions.administrator
    return commands.check(predicate)

# --- Fake-perm markers (for logs) --------------------------------------------
def _set_fp_used(ctx: commands.Context, val: bool) -> None:
    setattr(ctx, "_used_fakeperm", bool(val))

def fp_used(ctx: commands.Context) -> bool:
    return bool(getattr(ctx, "_used_fakeperm", False))

def fp_tag(ctx: commands.Context) -> str:
    """Return ' [fp]' when the command was authorized via fake perms."""
    return " [fp]" if fp_used(ctx) else ""

# --- Gate: allow real perms OR a fake-perm grant -----------------------------
def perm_or_fp(perm_name: str, **discord_perms) -> Callable:
    """
    Example:
      @perm_or_fp("ban", ban_members=True)
      async def ban(...)

    If user lacks the real perms in this channel but has a row
    in table fakeperms(guild_id,user_id,perm=perm_name), allow
    and mark the context so logs can add ' [fp]'.
    """
    async def predicate(ctx: commands.Context):
        _set_fp_used(ctx, False)
        if not ctx.guild:
            return False

        # 1) Check real channel permissions first
        chan_perms: discord.Permissions = ctx.channel.permissions_for(ctx.author)  # type: ignore
        real_ok = all(
            (not needed) or bool(getattr(chan_perms, key, False))
            for key, needed in discord_perms.items()
        )
        if real_ok:
            return True

        # 2) Check fakeperms table
        row = await ctx.bot.db.fetchrow(
            "SELECT 1 FROM fakeperms WHERE guild_id=? AND user_id=? AND perm=?",
            ctx.guild.id, ctx.author.id, perm_name
        )
        if row:
            _set_fp_used(ctx, True)
            return True
        return False
    return commands.check(predicate)
