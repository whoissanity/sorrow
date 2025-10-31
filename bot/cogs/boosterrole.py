from __future__ import annotations
from typing import Optional
import discord
from discord.ext import commands
import xml.etree.ElementTree as ET

class BoosterRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _has_booster(self, member: discord.Member) -> bool:
        r = member.guild.premium_subscriber_role
        return (r in member.roles) if r else (member.premium_since is not None)

    async def _get_role(self, guild: discord.Guild, user_id: int) -> Optional[discord.Role]:
        row = await self.bot.db.fetchrow("SELECT role_id FROM booster_roles WHERE guild_id=? AND user_id=?", guild.id, user_id)
        if not row:
            return None
        role = guild.get_role(int(row["role_id"]))
        return role

    @commands.group(name="boosterrole", invoke_without_command=True)
    async def boosterrole(self, ctx: commands.Context):
        member: discord.Member = ctx.author  # type: ignore
        if not isinstance(member, discord.Member):
            await ctx.send("server only."); return
        if not self._has_booster(member):
            await ctx.send("you must be a server booster to use this."); return
        role = await self._get_role(ctx.guild, member.id)
        if role:
            if role not in member.roles:
                try: await member.add_roles(role, reason="boosterrole ensure assign")
                except Exception: pass
            await ctx.send("ok."); return
        try:
            role_name = f"{member.name}'s Booster Role"
            role = await ctx.guild.create_role(name=role_name, reason="boosterrole create", hoist=False, mentionable=True)
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO booster_roles(guild_id, user_id, role_id) VALUES(?, ?, ?)",
                ctx.guild.id, member.id, role.id
            )
            await member.add_roles(role, reason="boosterrole assign")
            await ctx.send("ok.")
        except discord.Forbidden:
            await ctx.send("i don't have permission to manage roles.")
        except Exception:
            await ctx.send("failed to create booster role.")

    @boosterrole.command(name="rename")
    async def boosterrole_rename(self, ctx: commands.Context, *, new_name: str):
        member: discord.Member = ctx.author  # type: ignore
        if not isinstance(member, discord.Member):
            await ctx.send("server only."); return
        role = await self._get_role(ctx.guild, member.id)
        if not role:
            await ctx.send("no booster role found. run `,boosterrole` first."); return
        try:
            await role.edit(name=new_name, reason="boosterrole rename")
            await ctx.send("ok.")
        except discord.Forbidden:
            await ctx.send("i don't have permission to edit roles.")
        except Exception:
            await ctx.send("failed to rename booster role.")

async def setup(bot: commands.Bot):
    await bot.add_cog(BoosterRole(bot))
