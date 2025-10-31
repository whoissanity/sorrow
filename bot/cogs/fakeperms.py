from __future__ import annotations
import discord
from discord.ext import commands
from bot.utils.logger import log_mod
from bot.utils.checks import is_guild_owner_or_admin
import xml.etree.ElementTree as ET

class FakePerms(commands.Cog, name="FakePerms"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="fpadd", usage="@user <perm> [perm2 ...]")
    @is_guild_owner_or_admin()
    async def fpadd(self, ctx: commands.Context, user: discord.Member, *perms: str):
        if not perms:
            return await ctx.send("usage: ,fpadd @user <perm> [perm2 ...]")
        added = []
        for p in perms:
            p = p.lower()
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO fakeperms(guild_id,user_id,perm) VALUES(?,?,?)",
                ctx.guild.id, user.id, p
            )
            added.append(p)
        await log_mod(self.bot, ctx.guild, f"fakeperm add: {ctx.author} -> {user} [{', '.join(added)}]")
        await ctx.send("ok.")

    @commands.command(name="fpremove", usage="@user <perm> [perm2 ...]")
    @is_guild_owner_or_admin()
    async def fpremove(self, ctx: commands.Context, user: discord.Member, *perms: str):
        if not perms:
            return await ctx.send("usage: ,fpremove @user <perm> [perm2 ...]")
        removed = []
        for p in perms:
            p = p.lower()
            await self.bot.db.execute(
                "DELETE FROM fakeperms WHERE guild_id=? AND user_id=? AND perm=?",
                ctx.guild.id, user.id, p
            )
            removed.append(p)
        await log_mod(self.bot, ctx.guild, f"fakeperm remove: {ctx.author} -> {user} [{', '.join(removed)}]")
        await ctx.send("ok.")

    @commands.command(name="fplist", usage="@user")
    @is_guild_owner_or_admin()
    async def fplist(self, ctx: commands.Context, user: discord.Member):
        rows = await self.bot.db.fetchall(
            "SELECT perm FROM fakeperms WHERE guild_id=? AND user_id=?",
            ctx.guild.id, user.id
        )
        if not rows:
            return await ctx.send("none")
        await ctx.send(", ".join(sorted(r["perm"] for r in rows)))

async def setup(bot: commands.Bot):
    await bot.add_cog(FakePerms(bot))
