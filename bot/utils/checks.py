from __future__ import annotations
from typing import Any, Dict
from discord.ext import commands

def is_guild_owner_or_admin():
    async def predicate(ctx: commands.Context):
        if ctx.guild is None:
            return False
        if ctx.author == ctx.guild.owner:
            return True
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

def perm_or_fp(fp_name: str, **perms_required: bool):
    """
    Passes if:
      - user has administrator, OR
      - user has all given discord perms, OR
      - user has fake perm 'fp_name' in DB for this guild.
    """
    async def predicate(ctx: commands.Context):
        if ctx.guild is None:
            return False
        # Admin bypass
        if ctx.author.guild_permissions.administrator:
            return True
        # Real perms
        perms = ctx.author.guild_permissions
        has_all = True
        for k, v in perms_required.items():
            if v and not getattr(perms, k, False):
                has_all = False
                break
        if has_all:
            return True
        # Fake perm
        row = await ctx.bot.db.fetchrow(
            "SELECT 1 FROM fake_perms WHERE guild_id=? AND user_id=? AND perm=?",
            ctx.guild.id, ctx.author.id, fp_name.lower()
        )
        return row is not None
    return commands.check(predicate)
