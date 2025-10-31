# bot/cogs/utility.py (top of file)
from __future__ import annotations
from typing import Optional, List

from datetime import datetime, timezone

import discord
from discord.ext import commands

# Safe import: works even if checks.fp_tag is missing
try:
    from bot.utils.checks import perm_or_fp, fp_tag as _fp_tag
except Exception:
    from bot.utils.checks import perm_or_fp  # type: ignore
    def _fp_tag(ctx):
        return " [fp]" if getattr(ctx, "_used_fakeperm", False) else ""

from bot.utils.durations import parse_duration
from bot.utils.logger import log_mod

def _fp(ctx: commands.Context) -> str:
    return _fp_tag(ctx)


class Utility(commands.Cog, name="Utility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ----- autorole -----
    @commands.group(name="autorole", invoke_without_command=True)
    @commands.has_guild_permissions(administrator=True)
    async def autorole(self, ctx: commands.Context):
        row = await self.bot.db.fetchrow("SELECT role_id FROM autorole WHERE guild_id=?", ctx.guild.id)
        if not row or not row["role_id"]:
            return await ctx.send("autorole: not set")
        r = ctx.guild.get_role(int(row["role_id"]))
        await ctx.send(f"autorole: {r.mention if r else row['role_id']}")

    @autorole.command(name="set", usage="<@role>")
    @commands.has_guild_permissions(administrator=True)
    async def autorole_set(self, ctx: commands.Context, role: discord.Role):
        # Manual upsert for maximum SQLite compatibility
        row = await self.bot.db.fetchrow("SELECT 1 FROM autorole WHERE guild_id=?", ctx.guild.id)
        if row:
            await self.bot.db.execute("UPDATE autorole SET role_id=? WHERE guild_id=?", role.id, ctx.guild.id)
        else:
            await self.bot.db.execute("INSERT INTO autorole(guild_id, role_id) VALUES(?, ?)", ctx.guild.id, role.id)
        await ctx.send("ok.")

    @autorole.command(name="clear")
    @commands.has_guild_permissions(administrator=True)
    async def autorole_clear(self, ctx: commands.Context):
        await self.bot.db.execute("DELETE FROM autorole WHERE guild_id=?", ctx.guild.id)
        await ctx.send("ok.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Requires intents.members enabled in code + dev portal
        row = await self.bot.db.fetchrow("SELECT role_id FROM autorole WHERE guild_id=?", member.guild.id)
        if not row or not row["role_id"]:
            return
        role = member.guild.get_role(int(row["role_id"]))
        if role:
            try:
                await member.add_roles(role, reason="Auto role")
            except Exception:
                pass

    # ----- role add/remove -----
    @commands.command(name="role", usage="@user @role")
    @perm_or_fp("role", manage_roles=True)
    async def role_add(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        try:
            await member.add_roles(role, reason=f"Role cmd by {ctx.author}")
            await log_mod(self.bot, ctx.guild, f"role add: {ctx.author} -> {member} +{role.name}{_fp(ctx)}")
            await ctx.send("ok.")
        except Exception:
            await ctx.send("failed.")

    @commands.command(name="rmrole", usage="@user @role")
    @perm_or_fp("role", manage_roles=True)
    async def role_remove(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        try:
            await member.remove_roles(role, reason=f"Role rm cmd by {ctx.author}")
            await log_mod(self.bot, ctx.guild, f"role remove: {ctx.author} -> {member} -{role.name}{_fp(ctx)}")
            await ctx.send("ok.")
        except Exception:
            await ctx.send("failed.")

    # ----- nick -----
    @commands.command(name="nick", usage="[member] <new_nick>")
    @perm_or_fp("manage_nicknames", manage_nicknames=True)
    async def nick(self, ctx: commands.Context, member: Optional[discord.Member], *, new_nick: str):
        target = member or ctx.author
        try:
            await target.edit(nick=new_nick, reason=f"Nick by {ctx.author}")
            await log_mod(self.bot, ctx.guild, f"nick: {ctx.author} -> {target} '{new_nick}'{_fp(ctx)}")
            await ctx.send("ok.")
        except Exception:
            await ctx.send("failed.")

    # ----- imgonly toggle + listener -----
    @commands.command(name="imgonly", usage="on|off")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def imgonly(self, ctx: commands.Context, mode: str):
        mode = mode.lower()
        if mode not in ("on","off"):
            return await ctx.send("usage: ,imgonly on|off")
        if mode == "on":
            await self.bot.db.execute("INSERT OR IGNORE INTO imgonly_channels(guild_id, channel_id) VALUES(?,?)", ctx.guild.id, ctx.channel.id)
        else:
            await self.bot.db.execute("DELETE FROM imgonly_channels WHERE guild_id=? AND channel_id=?", ctx.guild.id, ctx.channel.id)
        await log_mod(self.bot, ctx.guild, f"imgonly {mode}: {ctx.author} -> #{ctx.channel.name}{_fp(ctx)}")
        await ctx.send("ok.")

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.guild is None or msg.author.bot:
            return
        # bypass staff with manage_messages
        if msg.channel.permissions_for(msg.author).manage_messages:
            return
        row = await self.bot.db.fetchrow("SELECT 1 FROM imgonly_channels WHERE guild_id=? AND channel_id=?", msg.guild.id, msg.channel.id)
        if not row:
            return
        # Allow only messages with at least one image attachment (caption allowed)
        has_image = False
        for a in msg.attachments:
            if a.content_type and a.content_type.startswith("image/"):
                has_image = True
                break
        if not has_image:
            try: await msg.delete()
            except Exception: pass

    # ----- lock / unlock / lockdown -----
    @commands.command(name="lock", usage="[channel]")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        ch = channel or ctx.channel  # type: ignore
        try:
            await ch.set_permissions(ctx.guild.default_role, send_messages=False)
            await log_mod(self.bot, ctx.guild, f"lock: {ctx.author} -> #{ch.name}{_fp(ctx)}")
            await ctx.send("ok.")
        except Exception:
            await ctx.send("failed.")

    @commands.command(name="unlock", usage="[channel]")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        ch = channel or ctx.channel  # type: ignore
        try:
            await ch.set_permissions(ctx.guild.default_role, send_messages=None)
            await log_mod(self.bot, ctx.guild, f"unlock: {ctx.author} -> #{ch.name}{_fp(ctx)}")
            await ctx.send("ok.")
        except Exception:
            await ctx.send("failed.")



    # ----- moveall -----
    @commands.command(name="moveall", usage="<from vc> <to vc>")
    @perm_or_fp("move_members", move_members=True)
    async def moveall(self, ctx: commands.Context, source: discord.VoiceChannel, dest: discord.VoiceChannel):
        moved = 0
        for m in list(source.members):
            try:
                await m.move_to(dest, reason=f"moveall by {ctx.author}")
                moved += 1
            except Exception:
                pass
        await log_mod(self.bot, ctx.guild, f"moveall: {ctx.author} -> {source.name} -> {dest.name} ({moved}){_fp(ctx)}")
        await ctx.send("ok.")

    # ----- reminders -----
    @commands.group(name="remind", invoke_without_command=True)
    async def remind(self, ctx: commands.Context, duration: Optional[str] = None, *, text: Optional[str] = None):
        if duration is None or text is None:
            return await ctx.send("usage: ,remind <duration> <text> | ,remind list | ,remind remove <id>")
        try:
            td = parse_duration(duration)
        except Exception:
            return await ctx.send("invalid duration.")
        fire_at = (discord.utils.utcnow() + td).isoformat()
        await self.bot.db.execute(
            "INSERT INTO reminders(guild_id, channel_id, user_id, fire_at, text) VALUES(?, ?, ?, ?, ?)",
            ctx.guild.id, ctx.channel.id, ctx.author.id, fire_at, text
        )
        await ctx.send("ok.")

    @remind.command(name="list")
    async def remind_list(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall("SELECT id, fire_at, text FROM reminders WHERE user_id=? ORDER BY fire_at ASC", ctx.author.id)
        if not rows:    
            return await ctx.send("no reminders.")
        lines = [f"#{r['id']} at {r['fire_at']} : {r['text']}" for r in rows[:25]]
        await ctx.send("\n".join(lines))

    @remind.command(name="remove", usage="<id>")
    async def remind_remove(self, ctx: commands.Context, rid: int):
        await self.bot.db.execute("DELETE FROM reminders WHERE id=? AND user_id=?", rid, ctx.author.id)
        await ctx.send("ok.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
