# bot/cogs/moderation.py
from __future__ import annotations
from typing import Optional, List, Tuple
from datetime import datetime, timezone

import discord
from discord.ext import commands

# Safe import: tolerate missing fp_tag export
try:
    from bot.utils.checks import perm_or_fp, fp_tag as _fp_tag
except Exception:
    from bot.utils.checks import perm_or_fp  # type: ignore
    def _fp_tag(ctx):
        return " [fp]" if getattr(ctx, "_used_fakeperm", False) else ""

from bot.utils.durations import parse_duration
from bot.utils.logger import log_mod

BASELINE_ROLE_ID_FOR_STRIPSTAFF = 1429416874252308570  # change if needed

# ---------- helpers ----------


# helpers for hide/unhide
def _resolve_channel(ctx: commands.Context, channel: Optional[discord.abc.GuildChannel]):
    ch = channel or ctx.channel
    if isinstance(ch, discord.Thread):
        if ch.parent is None:
            raise commands.BadArgument("thread has no parent.")
        return ch.parent
    if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel, discord.CategoryChannel)):
        raise commands.BadArgument("unsupported channel type.")
    return ch

async def _bot_can_manage(ctx: commands.Context, ch: discord.abc.GuildChannel) -> bool:
    me = ctx.guild.me
    if not me:
        return False
    if not ch.permissions_for(me).manage_channels:
        await ctx.send("i need **Manage Channels**.")
        return False
    return True




def _fp(ctx: commands.Context) -> str:
    return _fp_tag(ctx)

def _hierarchy_block(ctx: commands.Context, target: discord.Member) -> Optional[str]:
    if ctx.author != ctx.guild.owner and target.top_role >= ctx.author.top_role:
        return "you cannot act on members with equal or higher role."
    me = ctx.guild.me
    if me and target.top_role >= me.top_role:
        return "i cannot act on that member due to role hierarchy."
    return None



def _find_mute_roles(guild: discord.Guild) -> List[discord.Role]:
    names = {"Text Muted", "Image Muted", "Reaction Muted"}
    return [r for r in guild.roles if r.name in names]

# ---- helpers ----
def _not_pinned(_: discord.Message) -> bool:
    return not _.pinned

def _and(*checks):
    def _inner(m: discord.Message) -> bool:
        return all(c(m) for c in checks)
    return _inner


# ---- purge (base + subcmds) ----
def _clamp(self, amount: int) -> int:
    try:
        n = int(amount)
    except Exception:
        n = 1
    return max(1, min(100, n))# ---- helpers ----
def _not_pinned(_: discord.Message) -> bool:
    return not _.pinned

def _and(*checks):
    def _inner(m: discord.Message) -> bool:
        return all(c(m) for c in checks)
    return _inner


# ---- helpers ----
def _not_pinned(_: discord.Message) -> bool:
    return not _.pinned

def _and(*checks):
    def _inner(m: discord.Message) -> bool:
        return all(c(m) for c in checks)
    return _inner


# ---- purge (base + subcmds) ----
def _clamp(self, amount: int) -> int:
    try:
        n = int(amount)
    except Exception:
        n = 1
    return max(1, min(100, n))

@commands.group(name="purge", invoke_without_command=True)
@perm_or_fp("purge", manage_messages=True)
async def purge(self, ctx: commands.Context, amount: int):
    n = self._clamp(amount)
    try:
        # never touch pinned
        await ctx.channel.purge(limit=n + 1, check=_not_pinned)
        await log_mod(self.bot, ctx.guild, f"purge: {ctx.author} -> {n} messages in #{ctx.channel.name}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to purge.")

@purge.command(name="bot")
@perm_or_fp("purge", manage_messages=True)
async def purge_bot(self, ctx: commands.Context, amount: int):
    n = self._clamp(amount)
    try:
        await ctx.channel.purge(
            limit=n + 1,
            check=_and(_not_pinned, lambda m: m.author.bot),
        )
        await log_mod(self.bot, ctx.guild, f"purge bot: {ctx.author} -> {n} in #{ctx.channel.name}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to purge.")

@purge.command(name="human")
@perm_or_fp("purge", manage_messages=True)
async def purge_human(self, ctx: commands.Context, amount: int):
    """
    Delete the last <amount> messages authored by non-bot users, excluding pinned.
    Usage: ,purge human 25
    """
    n = self._clamp(amount)
    try:
        await ctx.channel.purge(
            limit=n + 1,
            check=_and(_not_pinned, lambda m: not getattr(m.author, "bot", False)),
        )
        await log_mod(self.bot, ctx.guild, f"purge human: {ctx.author} -> {n} in #{ctx.channel.name}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to purge.")

@purge.command(name="embeds")
@perm_or_fp("purge", manage_messages=True)
async def purge_embeds(self, ctx: commands.Context, amount: int):
    n = self._clamp(amount)
    try:
        await ctx.channel.purge(
            limit=n + 1,
            check=_and(_not_pinned, lambda m: bool(m.embeds)),
        )
        await log_mod(self.bot, ctx.guild, f"purge embeds: {ctx.author} -> {n} in #{ctx.channel.name}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to purge.")

@purge.command(name="reaction")
@perm_or_fp("purge", manage_messages=True)
async def purge_reaction(self, ctx: commands.Context, target: Optional[str] = None):
    msg = None
    if not target and ctx.message.reference and ctx.message.reference.message_id:
        try:
            msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception:
            pass
    elif target:
        if target.isdigit():
            try:
                msg = await ctx.channel.fetch_message(int(target))
            except Exception:
                pass
        elif "discord.com/channels/" in target:
            try:
                parts = target.rstrip("/").split("/")
                ch_id = int(parts[-2])
                msg_id = int(parts[-1])
                ch = ctx.guild.get_channel(ch_id)
                if isinstance(ch, discord.TextChannel):
                    msg = await ch.fetch_message(msg_id)
            except Exception:
                pass
    if not msg:
        return await ctx.send("unable to find message. reply to it or pass a link/id.")
    try:
        await msg.clear_reactions()
        await log_mod(self.bot, ctx.guild, f"purge reactions: {ctx.author} -> https://discord.com/channels/{ctx.guild.id}/{msg.channel.id}/{msg.id}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to clear reactions.")

@purge.command(name="all")
@perm_or_fp("purge", manage_messages=True)
async def purge_all(self, ctx: commands.Context):
    ch: discord.TextChannel = ctx.channel  # type: ignore
    before = None
    try:
        while True:
            batch = [m async for m in ch.history(limit=100, before=before)]
            if not batch:
                break

            now = discord.utils.utcnow()
            # split into <14d (eligible for bulk delete) and older
            fresh = [m for m in batch if (now - m.created_at.replace(tzinfo=timezone.utc)).days < 14 and not m.pinned]
            if len(fresh) >= 2:
                try:
                    await ch.delete_messages(fresh)
                except Exception:
                    for m in fresh:
                        try:
                            await m.delete()
                        except Exception:
                            pass

            older = [m for m in batch if (m not in fresh) and not m.pinned]
            for m in older:
                try:
                    await m.delete()
                except Exception:
                    pass

            before = batch[-1]
        await log_mod(self.bot, ctx.guild, f"purge all: {ctx.author} -> #{ch.name}{_fp(ctx)}")
    except Exception:
        await ctx.send("failed to purge.")


async def _resolve_member_or_user(ctx: commands.Context, arg: str) -> Tuple[Optional[discord.Member], Optional[discord.User]]:
    """Return (member,user) where member is None if not in guild, user is always set if resolvable."""
    if not arg:
        return None, None
    s = arg.strip()
    if s.startswith("<@") and s.endswith(">"):
        s = s.strip("<@!>")
    member = None
    user = None
    if s.isdigit():
        mid = int(s)
        member = ctx.guild.get_member(mid)
        try:
            user = await ctx.bot.fetch_user(mid)
        except Exception:
            user = member
        return member, user
    try:
        member = await commands.MemberConverter().convert(ctx, arg)
        return member, member
    except Exception:
        pass
    try:
        user = await commands.UserConverter().convert(ctx, arg)
        return None, user
    except Exception:
        return None, None

async def _save_snapshot(bot, guild_id: int, user_id: int, roles: List[discord.Role]):
    role_ids = ",".join(str(r.id) for r in roles if r and r.managed is False)
    await bot.db.execute(
        "INSERT OR REPLACE INTO role_snapshots(guild_id, user_id, roles, updated_at) VALUES(?, ?, ?, ?)",
        guild_id, user_id, role_ids, datetime.now(timezone.utc).isoformat()
    )

# ---------- cog ----------
class Moderation(commands.Cog, name="Moderation"):
    """All moderation commands. Replies are plain text ('ok' or short errors), except warnings list (embed)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="hide", usage="[channel]")
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def hide(self, ctx: commands.Context, channel: Optional[discord.abc.GuildChannel] = None):
        ch = _resolve_channel(ctx, channel)
        if not await _bot_can_manage(ctx, ch):
            return
        try:
            await ch.set_permissions(ctx.guild.default_role, view_channel=False, reason=f"hide by {ctx.author}")
            await ctx.send("ok")
        except Exception as e:
            await ctx.send(f"error: {e}")

    @commands.command(name="unhide", usage="[channel]")
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def unhide(self, ctx: commands.Context, channel: Optional[discord.abc.GuildChannel] = None):
        ch = _resolve_channel(ctx, channel)
        if not await _bot_can_manage(ctx, ch):
            return
        try:
            await ch.set_permissions(ctx.guild.default_role, view_channel=True, reason=f"unhide by {ctx.author}")
            await ctx.send("ok")
        except Exception as e:
            await ctx.send(f"error: {e}")





    # ---- kick ----
    @commands.command(name="kick", usage="@user [reason]")
    @perm_or_fp("kick", kick_members=True)
    async def kick(self, ctx: commands.Context, target: str, *, reason: Optional[str] = None):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in this server to kick.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        try:
            await member.kick(reason=reason)
            await log_mod(self.bot, ctx.guild, f"kick: {ctx.author} -> {member} | {reason or ''}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to kick that member.")
        except Exception:
            await ctx.send("failed to kick member.")

    # ---- ban / unban ----
    @commands.command(name="ban", usage="@user|id [reason]")
    @perm_or_fp("ban", ban_members=True)
    async def ban(self, ctx: commands.Context, target: str, *, reason: Optional[str] = None):
        member, user = await _resolve_member_or_user(ctx, target)
        if member:
            msg = _hierarchy_block(ctx, member)
            if msg:
                return await ctx.send(msg)
        if user is None:
            return await ctx.send("user not found.")
        try:
            await ctx.guild.ban(user, reason=reason, delete_message_seconds=0)
            await log_mod(self.bot, ctx.guild, f"ban: {ctx.author} -> {user} | {reason or ''}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to ban that user.")
        except Exception:
            await ctx.send("failed to ban user.")

    @commands.command(name="unban", usage="@user|id")
    @perm_or_fp("ban", ban_members=True)
    async def unban(self, ctx: commands.Context, target: str):
        _, user = await _resolve_member_or_user(ctx, target)
        if user is None:
            return await ctx.send("user not found.")
        try:
            await ctx.guild.unban(user, reason="Unban command")
            await log_mod(self.bot, ctx.guild, f"unban: {ctx.author} -> {user}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.NotFound:
            await ctx.send("user not banned.")
        except discord.Forbidden:
            await ctx.send("i don't have permission to unban.")
        except Exception:
            await ctx.send("failed to unban.")

    @commands.command(name="unbanall")
    @perm_or_fp("ban", ban_members=True)
    async def unbanall(self, ctx: commands.Context):
        try:
            bans = [entry async for entry in ctx.guild.bans(limit=None)]
            for entry in bans:
                try:
                    await ctx.guild.unban(entry.user, reason="unbanall")
                except Exception:
                    pass
            await log_mod(self.bot, ctx.guild, f"unbanall: by {ctx.author}{_fp(ctx)}")
        except Exception:
            pass  # silent success like purge

    @commands.command(name="banrecent", usage="<duration|N> [reason]")
    @perm_or_fp("ban", ban_members=True)
    async def banrecent(self, ctx: commands.Context, first: str, *, reason: Optional[str] = None):
        to_ban: List[discord.Member] = []
        now = discord.utils.utcnow()
        try:
            n = int(first)
            joined = sorted([m for m in ctx.guild.members if m.joined_at],
                            key=lambda m: m.joined_at or now, reverse=True)
            to_ban = joined[:n]
        except ValueError:
            try:
                td = parse_duration(first)
            except Exception:
                return await ctx.send("invalid window.")
            cutoff = now - td
            to_ban = [m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff]
        count = 0
        for m in to_ban:
            try:
                if m == ctx.author or (ctx.author != ctx.guild.owner and m.top_role >= ctx.author.top_role):
                    continue
                await ctx.guild.ban(m, reason=reason or "banrecent", delete_message_seconds=0)
                count += 1
            except Exception:
                pass
        await log_mod(self.bot, ctx.guild, f"banrecent: {ctx.author} -> {count} users | {reason or ''}{_fp(ctx)}")
        await ctx.send("ok")

    # ---- temp/soft/hard ban ----
    @commands.command(name="tempban", usage="@user <duration> [reason]")
    @perm_or_fp("ban", ban_members=True)
    async def tempban(self, ctx: commands.Context, target: str, duration: str, *, reason: Optional[str] = None):
        member, user = await _resolve_member_or_user(ctx, target)
        if member:
            msg = _hierarchy_block(ctx, member)
            if msg:
                return await ctx.send(msg)
        if user is None:
            return await ctx.send("user not found.")
        try:
            td = parse_duration(duration)
        except Exception:
            return await ctx.send("invalid duration.")
        try:
            await ctx.guild.ban(user, reason=reason or f"Tempban for {duration}", delete_message_seconds=0)
            unban_at = (discord.utils.utcnow() + td).isoformat()
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO temp_bans(guild_id, user_id, unban_at, reason) VALUES(?, ?, ?, ?)",
                ctx.guild.id, user.id, unban_at, reason or ""
            )
            await log_mod(self.bot, ctx.guild, f"tempban: {ctx.author} -> {user} | {duration} | {reason or ''}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to ban that member.")
        except Exception:
            await ctx.send("failed to tempban member.")

    @commands.command(name="softban", usage="@user [reason]")
    @perm_or_fp("ban", ban_members=True)
    async def softban(self, ctx: commands.Context, target: str, *, reason: Optional[str] = None):
        member, user = await _resolve_member_or_user(ctx, target)
        if member:
            msg = _hierarchy_block(ctx, member)
            if msg:
                return await ctx.send(msg)
        if user is None:
            return await ctx.send("user not found.")
        try:
            await ctx.guild.ban(user, reason=reason or "Softban", delete_message_seconds=604800)
            await ctx.guild.unban(user, reason="Softban complete")
            await log_mod(self.bot, ctx.guild, f"softban: {ctx.author} -> {user} | {reason or ''}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to ban that member.")
        except Exception:
            await ctx.send("failed to softban member.")

    @commands.command(name="hardban", usage="@user [reason]")
    @perm_or_fp("ban", ban_members=True)
    async def hardban(self, ctx: commands.Context, target: str, *, reason: Optional[str] = None):
        member, user = await _resolve_member_or_user(ctx, target)
        if member:
            msg = _hierarchy_block(ctx, member)
            if msg:
                return await ctx.send(msg)
        if user is None:
            return await ctx.send("user not found.")
        try:
            await ctx.guild.ban(user, reason=reason or "Hardban", delete_message_seconds=604800)
            await log_mod(self.bot, ctx.guild, f"hardban: {ctx.author} -> {user} | {reason or ''}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to ban that member.")
        except Exception:
            await ctx.send("failed to hardban member.")

    # ---- mute variants ----
    @commands.command(name="mute", usage="@user|id")
    @perm_or_fp("mute", moderate_members=True, manage_roles=True)
    async def mute(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to mute.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        roles = _find_mute_roles(ctx.guild)
        if not roles:
            return await ctx.send("run `,setup` first to create mute roles.")
        try:
            await _save_snapshot(self.bot, ctx.guild.id, member.id, [r for r in member.roles if r != ctx.guild.default_role])
            await member.add_roles(*roles, reason="Mute")
            await log_mod(self.bot, ctx.guild, f"mute: {ctx.author} -> {member}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to manage roles for that member.")
        except Exception:
            await ctx.send("failed to mute member.")

    @commands.command(name="unmute", usage="@user")
    @perm_or_fp("mute", moderate_members=True, manage_roles=True)
    async def unmute(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to unmute.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        names = {"Text Muted", "Image Muted", "Reaction Muted"}
        roles = [r for r in member.roles if r.name in names]
        try:
            if roles:
                await member.remove_roles(*roles, reason="Unmute")
            await log_mod(self.bot, ctx.guild, f"unmute: {ctx.author} -> {member}{_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to manage roles for that member.")
        except Exception:
            await ctx.send("failed to unmute member.")

    @commands.command(name="hardmute", usage="@user")
    @perm_or_fp("mute", moderate_members=True, manage_roles=True)
    async def hardmute(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to hardmute.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        mute_roles = _find_mute_roles(ctx.guild)
        if not mute_roles:
            return await ctx.send("run `,setup` first to create mute roles.")
        original_roles = [r for r in member.roles if r != ctx.guild.default_role]
        try:
            await _save_snapshot(self.bot, ctx.guild.id, member.id, original_roles)
            for r in original_roles:
                try:
                    await member.remove_roles(r, reason="Hardmute: remove roles")
                except Exception:
                    pass
            try:
                await member.add_roles(*mute_roles, reason="Hardmute: mute roles")
            except Exception:
                pass
            await log_mod(self.bot, ctx.guild, f"hardmute: {ctx.author} -> {member}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed to hardmute member.")

    @commands.command(name="unhardmute", usage="@user")
    @perm_or_fp("mute", moderate_members=True, manage_roles=True)
    async def unhardmute(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to unhardmute.")
        names = {"Text Muted", "Image Muted", "Reaction Muted"}
        mute_roles = [r for r in member.roles if r.name in names]
        try:
            if mute_roles:
                await member.remove_roles(*mute_roles, reason="Unhardmute: remove mute roles")
        except Exception:
            pass
        row = await self.bot.db.fetchrow("SELECT roles FROM role_snapshots WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
        if row:
            ids = [int(x) for x in row["roles"].split(",") if x]
            roles_to_add = [ctx.guild.get_role(rid) for rid in ids if ctx.guild.get_role(rid)]
            try:
                if roles_to_add:
                    await member.add_roles(*roles_to_add, reason="Unhardmute: restore roles")
            except Exception:
                pass
        await log_mod(self.bot, ctx.guild, f"unhardmute: {ctx.author} -> {member}{_fp(ctx)}")
        await ctx.send("ok")

    # ---- timeout / untimeout ----
    @commands.command(name="timeout", usage="@user|id <duration> [reason]")
    @perm_or_fp("timeout", moderate_members=True)
    async def timeout(self, ctx: commands.Context, target: str, duration: str, *, reason: Optional[str] = None):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to timeout.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        try:
            td = parse_duration(duration)
        except Exception:
            return await ctx.send("invalid duration.")
        try:
            if hasattr(member, "timeout_for"):
                await member.timeout_for(td, reason=reason)
            else:
                until = discord.utils.utcnow() + td
                await member.edit(timeout=until, reason=reason)
            await log_mod(self.bot, ctx.guild, f"timeout: {ctx.author} -> {member} | {duration} | {reason or ''}{_fp(ctx)}")
            await ctx.send(f"(**{member.name}** has been muted for {duration})")
        except discord.Forbidden:
            await ctx.send("i don't have permission to timeout that member.")
        except Exception:
            await ctx.send("failed to timeout member.")

    @commands.command(name="untimeout", usage="@user")
    @perm_or_fp("timeout", moderate_members=True)
    async def untimeout(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server to untimeout.")
        try:
            if hasattr(member, "timeout"):
                await member.timeout(None, reason="untimeout")
            else:
                await member.edit(timeout=None, reason="untimeout")
            await log_mod(self.bot, ctx.guild, f"untimeout: {ctx.author} -> {member}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed to untimeout member.")

    # ---- stripstaff / rolerestore ----
    @commands.command(name="stripstaff", usage="@user")
    @perm_or_fp("stripstaff", manage_roles=True, administrator=True)
    async def stripstaff(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server.")
        msg = _hierarchy_block(ctx, member)
        if msg:
            return await ctx.send(msg)
        base = ctx.guild.get_role(BASELINE_ROLE_ID_FOR_STRIPSTAFF)
        if not base:
            return await ctx.send("baseline role not found.")
        to_remove = [r for r in member.roles if r != ctx.guild.default_role and r.position > base.position]
        try:
            await _save_snapshot(self.bot, ctx.guild.id, member.id, [r for r in member.roles if r != ctx.guild.default_role])
            for r in to_remove:
                try:
                    await member.remove_roles(r, reason="stripstaff")
                except Exception:
                    pass
            await log_mod(self.bot, ctx.guild, f"stripstaff: {ctx.author} -> {member} ({len(to_remove)} roles){_fp(ctx)}")
            await ctx.send("ok")
        except discord.Forbidden:
            await ctx.send("i don't have permission to remove roles.")
        except Exception:
            await ctx.send("failed to strip roles.")

    @commands.command(name="rolerestore", usage="@user")
    @perm_or_fp("role", manage_roles=True)
    async def rolerestore(self, ctx: commands.Context, target: str):
        member, _ = await _resolve_member_or_user(ctx, target)
        if not member:
            return await ctx.send("user must be in the server.")
        row = await self.bot.db.fetchrow("SELECT roles FROM role_snapshots WHERE guild_id=? AND user_id=?", ctx.guild.id, member.id)
        if not row:
            return await ctx.send("no snapshot.")
        ids = [int(x) for x in row["roles"].split(",") if x]
        roles_to_add = [ctx.guild.get_role(rid) for rid in ids if ctx.guild.get_role(rid)]
        try:
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Role restore")
            await log_mod(self.bot, ctx.guild, f"rolerestore: {ctx.author} -> {member}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed to restore roles.")

    # ---- purge (base + subcmds) ----
    def _clamp(self, amount: int) -> int:
        try:
            n = int(amount)
        except Exception:
            n = 1
        return max(1, min(100, n))

    @commands.group(name="purge", invoke_without_command=True)
    @perm_or_fp("purge", manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int):
        n = self._clamp(amount)
        try:
            await ctx.channel.purge(limit=n + 1)
            await log_mod(self.bot, ctx.guild, f"purge: {ctx.author} -> {n} messages in #{ctx.channel.name}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to purge.")

    @purge.command(name="bot")
    @perm_or_fp("purge", manage_messages=True)
    async def purge_bot(self, ctx: commands.Context, amount: int):
        n = self._clamp(amount)
        try:
            await ctx.channel.purge(limit=n + 1, check=lambda m: m.author.bot)
            await log_mod(self.bot, ctx.guild, f"purge bot: {ctx.author} -> {n} in #{ctx.channel.name}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to purge.")

    @purge.command(name="embeds")
    @perm_or_fp("purge", manage_messages=True)
    async def purge_embeds(self, ctx: commands.Context, amount: int):
        n = self._clamp(amount)
        try:
            await ctx.channel.purge(limit=n + 1, check=lambda m: bool(m.embeds))
            await log_mod(self.bot, ctx.guild, f"purge embeds: {ctx.author} -> {n} in #{ctx.channel.name}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to purge.")

    @purge.command(name="reaction")
    @perm_or_fp("purge", manage_messages=True)
    async def purge_reaction(self, ctx: commands.Context, target: Optional[str] = None):
        msg = None
        if not target and ctx.message.reference and ctx.message.reference.message_id:
            try:
                msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except Exception:
                pass
        elif target:
            if target.isdigit():
                try:
                    msg = await ctx.channel.fetch_message(int(target))
                except Exception:
                    pass
            elif "discord.com/channels/" in target:
                try:
                    parts = target.rstrip("/").split("/")
                    ch_id = int(parts[-2])
                    msg_id = int(parts[-1])
                    ch = ctx.guild.get_channel(ch_id)
                    if isinstance(ch, discord.TextChannel):
                        msg = await ch.fetch_message(msg_id)
                except Exception:
                    pass
        if not msg:
            return await ctx.send("unable to find message. reply to it or pass a link/id.")
        try:
            await msg.clear_reactions()
            await log_mod(self.bot, ctx.guild, f"purge reactions: {ctx.author} -> https://discord.com/channels/{ctx.guild.id}/{msg.channel.id}/{msg.id}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to clear reactions.")

    @purge.command(name="all")
    @perm_or_fp("purge", manage_messages=True)
    async def purge_all(self, ctx: commands.Context):
        ch: discord.TextChannel = ctx.channel  # type: ignore
        before = None
        try:
            while True:
                batch = [m async for m in ch.history(limit=100, before=before)]
                if not batch:
                    break
                now = discord.utils.utcnow()
                fresh = [m for m in batch if (now - m.created_at.replace(tzinfo=timezone.utc)).days < 14]
                if len(fresh) >= 2:
                    try:
                        await ch.delete_messages(fresh)
                    except Exception:
                        for m in fresh:
                            try:
                                await m.delete()
                            except Exception:
                                pass
                older = [m for m in batch if m not in fresh]
                for m in older:
                    try:
                        await m.delete()
                    except Exception:
                        pass
                before = batch[-1]
            await log_mod(self.bot, ctx.guild, f"purge all: {ctx.author} -> #{ch.name}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to purge.")

    # ---- warn / warnings ----
    @commands.command(name="warn", usage="@user [reason]")
    @perm_or_fp("warn", moderate_members=True)
    async def warn(self, ctx: commands.Context, target: str, *, reason: Optional[str] = None):
        member, user = await _resolve_member_or_user(ctx, target)
        u = member or user
        if not u:
            return await ctx.send("user not found.")
        await self.bot.db.execute(
            "INSERT INTO warnings(guild_id, user_id, moderator, reason) VALUES(?, ?, ?, ?)",
            ctx.guild.id, u.id, ctx.author.id, reason or ""
        )
        await log_mod(self.bot, ctx.guild, f"warn: {ctx.author} -> {u} | {reason or ''}{_fp(ctx)}")
        await ctx.send("ok")

    @commands.command(name="warnings", usage="[@user]")
    async def warnings(self, ctx: commands.Context, target: Optional[str] = None):
        user = ctx.author
        if target:
            m, u = await _resolve_member_or_user(ctx, target)
            user = (m or u) or ctx.author
        rows = await self.bot.db.fetchall(
            "SELECT id, moderator, reason, created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id ASC",
            ctx.guild.id, user.id
        )
        e = discord.Embed(title=f"Warnings for {getattr(user, 'name', user)}", color=0x2b2d31)
        if not rows:
            e.description = "No warnings."
        else:
            for r in rows[:25]:
                mod = ctx.guild.get_member(int(r["moderator"]))
                mod_name = mod.name if mod else str(r["moderator"])
                e.add_field(name=f"warning #{r['id']}", value=f"{mod_name}: {r['reason'] or ''}", inline=False)
            e.set_footer(text=("sanity." if len(rows) <= 25 else f"{len(rows)} total. sanity."))
        await ctx.send(embed=e)

    # ---- nuke ----
    @commands.command(name="nuke")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def nuke(self, ctx: commands.Context):
        ch: discord.TextChannel = ctx.channel  # type: ignore
        pos = ch.position
        cat = ch.category
        name = ch.name
        perms = ch.overwrites
        try:
            new_ch = await ch.clone(reason=f"Nuke by {ctx.author}")
            await new_ch.edit(position=pos, category=cat, name=name, overwrites=perms)
            await ch.delete(reason="Nuked")
            await log_mod(self.bot, ctx.guild, f"nuke: {ctx.author} -> #{name}{_fp(ctx)}")
        except Exception:
            await ctx.send("failed to nuke channel.")

    # ---- echo ----
    @commands.command(name="echo", usage="<#channel> <text>")
    @perm_or_fp("manage_messages", manage_messages=True)
    async def echo(self, ctx: commands.Context, channel: discord.TextChannel, *, text: str):
        try:
            await channel.send(text)
            await log_mod(self.bot, ctx.guild, f"echo: {ctx.author} -> #{channel.name}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed.")

    # ---- slowmode ----
    @commands.group(name="slowmode", invoke_without_command=True)
    @perm_or_fp("manage_channels", manage_channels=True)
    async def slowmode(self, ctx: commands.Context):
        await ctx.send("usage: ,slowmode on <duration> | ,slowmode off")

    @slowmode.command(name="on")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def slowmode_on(self, ctx: commands.Context, duration: str):
        try:
            td = parse_duration(duration)
            seconds = int(td.total_seconds())
            await ctx.channel.edit(slowmode_delay=seconds)
            await log_mod(self.bot, ctx.guild, f"slowmode on: {ctx.author} -> {seconds}s in #{ctx.channel.name}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed.")

    @slowmode.command(name="off")
    @perm_or_fp("manage_channels", manage_channels=True)
    async def slowmode_off(self, ctx: commands.Context):
        try:
            await ctx.channel.edit(slowmode_delay=0)
            await log_mod(self.bot, ctx.guild, f"slowmode off: {ctx.author} -> #{ctx.channel.name}{_fp(ctx)}")
            await ctx.send("ok")
        except Exception:
            await ctx.send("failed.")

    # ---- permissions ----
    @commands.command(name="permissions", usage="[@user]")
    async def permissions(self, ctx: commands.Context, target: Optional[discord.Member] = None):
        m = target or ctx.author
        perms = ctx.channel.permissions_for(m)  # type: ignore
        allowed = [name for name, val in perms if val]
        denied  = [name for name, val in perms if not val]
        out = "allowed: " + ", ".join(sorted(allowed)) + "\ndenied: " + ", ".join(sorted(denied))
        await ctx.send(out)



    # ---- members of a role ----
    @commands.command(name="members", usage="@role")
    async def members(self, ctx: commands.Context, role: discord.Role):
        mems = [m.name for m in role.members]
        head = f"{len(mems)} member(s) in {role.name}"
        preview = ", ".join(mems[:50])
        tail = " ..." if len(mems) > 50 else ""
        await ctx.send(head + " — " + preview + tail)

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
