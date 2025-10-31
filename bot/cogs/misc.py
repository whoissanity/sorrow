# bot/cogs/misc.py
from __future__ import annotations

import os
import re
import random
import asyncio
import aiohttp
from typing import Optional, List, Tuple, Dict, Set
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import discord
from discord.ext import commands

# ---------- constants / helpers ----------
BOT_DISPLAY_NAME = "sorrow."
BOT_MADE_BY = "sanity."

def ISO(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "unknown"
    return f"<t:{int(dt.replace(tzinfo=timezone.utc).timestamp())}:R>"

EMOJI_MENTION_RE = re.compile(r"<(a?):([0-9A-Za-z_]{2,32}):([0-9]{15,25})>")

def _has_manage_expressions(perms: discord.Permissions) -> bool:
    return any(
        getattr(perms, attr, False)
        for attr in ("manage_emojis", "manage_emojis_and_stickers", "manage_guild_expressions")
    )

def _sanitize_name(name: Optional[str]) -> str:
    name = (name or "emoji").strip()
    name = re.sub(r"[^0-9A-Za-z_]", "_", name)
    name = name[:32] or "emoji"
    return name

async def _fetch_bytes(pe: discord.PartialEmoji) -> bytes:
    """
    Works across discord.py versions:
    - 2.x: pe.url is Asset (has .read())
    - 1.x: pe.url is str
    """
    url_attr = getattr(pe, "url", None)
    if url_attr is None:
        return b""
    if hasattr(url_attr, "read"):
        try:
            return await url_attr.read()
        except Exception:
            return b""
    url = str(url_attr)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return b""
                return await resp.read()
    except Exception:
        return b""

# Vanity matcher: require structured forms, not bare code
def _compile_vanity_regex(code: str) -> re.Pattern:
    """
    Matches ONLY these forms (case-insensitive), with boundaries:
      /vanity
      .gg/vanity
      discord.gg/vanity
      https://discord.gg/vanity  (http ok too)
    Bare 'vanity' alone does NOT match by design.
    """
    code_esc = re.escape(code)
    pat = rf"(?:^|[^a-z0-9])(?:(?:https?://)?discord\.gg/{code_esc}|\.gg/{code_esc}|/{code_esc})(?:[^a-z0-9]|$)"
    return re.compile(pat, re.IGNORECASE)

def _member_custom_status_text(member: discord.Member) -> str:
    """Return the member's Custom Status text if present (bio/About Me is NOT accessible to bots)."""
    for act in getattr(member, "activities", []) or []:
        if isinstance(act, discord.CustomActivity):
            if act.name:
                return str(act.name)
    return ""

# =========================
# Remind Mixin (adds ,remind)
# =========================
import time

class RemindMixin:
    """
    Adds:
      ,remind <duration> <reason>
      ,remind remove <id>
      ,remind list
    In-memory scheduling; DMs the author (falls back to channel mention if DMs closed).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reminders: Dict[str, Dict] = {}
        self._remind_tasks: Dict[str, asyncio.Task] = {}
        self._remind_lock = asyncio.Lock()
        self._dur_re = re.compile(r"(?P<num>\d+)\s*(?P<unit>[wdhms])", re.IGNORECASE)

        self._rep_last_announce = {}
    # --- helpers ---
    def _now(self) -> float:
        return time.time()

    def _fmt_ts(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return f"{dt:%Y-%m-%d %H:%M:%S} UTC • <t:{int(ts)}:R>"

    def _gen_id(self) -> str:
        n = int(self._now() * 1000)
        b36 = ""
        while n:
            n, r = divmod(n, 36)
            b36 = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + b36
        suffix = "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(3))
        return (b36 or "0")[-7:] + suffix

    def _parse_duration(self, s: str) -> Optional[int]:
        s = s.strip().lower()
        if not s:
            return None
        if s.isdigit():
            return max(1, int(s))  # seconds
        total = 0
        for m in self._dur_re.finditer(s):
            n = int(m.group("num")); u = m.group("unit")
            if u == "w": total += n * 7 * 24 * 3600
            elif u == "d": total += n * 24 * 3600
            elif u == "h": total += n * 3600
            elif u == "m": total += n * 60
            elif u == "s": total += n
        return total or None

    def _humanize(self, seconds: int) -> str:
        parts: List[str] = []
        for unit_seconds, label in [(7*24*3600, "w"), (24*3600, "d"), (3600, "h"), (60, "m"), (1, "s")]:
            q, seconds = divmod(seconds, unit_seconds)
            if q:
                parts.append(f"{q}{label}")
        return "".join(parts) or "0s"

    async def _schedule_task(self, rid: str):
        if rid in self._remind_tasks and not self._remind_tasks[rid].done():
            return
        self._remind_tasks[rid] = asyncio.create_task(self._reminder_worker(rid))

    async def _reminder_worker(self, rid: str):
        rec = self._reminders.get(rid)
        if not rec:
            return
        delay = max(0.0, rec["due_ts"] - self._now())
        try:
            await asyncio.sleep(delay)
            user = rec["user"]; reason = rec["reason"]
            try:
                dm = await user.create_dm()
                await dm.send(f"⏰ **Reminder:** {reason}\nID: `{rid}`")
            except discord.Forbidden:
                ch = rec.get("origin_channel")
                if isinstance(ch, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
                    try:
                        await ch.send(f"{user.mention} ⏰ **Reminder:** {reason}\nID: `{rid}`")
                    except Exception:
                        pass
        finally:
            async with self._remind_lock:
                self._reminders.pop(rid, None)
                t = self._remind_tasks.pop(rid, None)
                if t and not t.done():
                    t.cancel()

    # --- commands ---
    @commands.group(name="remind", invoke_without_command=True)
    @commands.guild_only()
    async def remind_group(self, ctx: commands.Context, duration_or_sub: str = None, *, reason: str = None):
        if not duration_or_sub:
            return await ctx.reply("Usage: `,remind <duration> <reason>` | `,remind remove <id>` | `,remind list`")

        sub = duration_or_sub.lower()
        if sub in {"remove", "delete", "del", "rm"}:
            return await ctx.reply("Use: `,remind remove <id>`")
        if sub == "list":
            return await self.remind_list(ctx)

        secs = self._parse_duration(duration_or_sub)
        if not secs or secs <= 0:
            return await ctx.reply("Invalid duration. Examples: `10m`, `1h30m`, `2d`, `45s`")
        if not reason:
            return await ctx.reply("Please provide a reason. Example: `,remind 10m take a break`")

        rid = self._gen_id()
        due_ts = self._now() + secs
        record = {
            "id": rid, "user": ctx.author, "user_id": ctx.author.id,
            "guild_id": ctx.guild.id, "origin_channel": ctx.channel,
            "created_ts": self._now(), "due_ts": due_ts, "reason": reason.strip(),
        }
        async with self._remind_lock:
            self._reminders[rid] = record
            await self._schedule_task(rid)

        await ctx.reply(
            f"✅ Reminder set for **{self._humanize(int(secs))}** "
            f"(**{self._fmt_ts(due_ts)}**). ID: `{rid}`"
        )

    @remind_group.command(name="remove", aliases=["delete", "del", "rm"])
    @commands.guild_only()
    async def remind_remove(self, ctx: commands.Context, reminder_id: str):
        rid = reminder_id.strip()
        async with self._remind_lock:
            rec = self._reminders.get(rid)
            if not rec:
                return await ctx.reply("I couldn’t find a pending reminder with that ID.")
            authorized = (
                rec["user_id"] == ctx.author.id
                or (ctx.guild and ctx.author.id == ctx.guild.owner_id)
                or (await self._is_bot_owner(ctx))
            )
            if not authorized:
                return await ctx.reply("You can only remove your own reminders.")
            self._reminders.pop(rid, None)
            t = self._remind_tasks.pop(rid, None)
            if t and not t.done():
                t.cancel()
        await ctx.reply(f"🗑️ Removed reminder `{rid}`.")

    @remind_group.command(name="list")
    @commands.guild_only()
    async def remind_list(self, ctx: commands.Context):
        items = [r for r in self._reminders.values() if r["guild_id"] == ctx.guild.id and r["user_id"] == ctx.author.id]
        if not items:
            return await ctx.reply("You have no pending reminders here.")
        items.sort(key=lambda r: r["due_ts"])
        lines = [
            f"• ID `{r['id']}` — in **{self._humanize(int(max(0, r['due_ts'] - self._now()))) }** "
            f"({self._fmt_ts(r['due_ts'])}) — {r['reason']}"
            for r in items
        ]
        await ctx.reply("**Your reminders:**\n" + "\n".join(lines))

    async def _is_bot_owner(self, ctx: commands.Context) -> bool:
        try:
            return await ctx.bot.is_owner(ctx.author)
        except Exception:
            return False

# =========================
# Misc Cog
# =========================
class Misc(RemindMixin, commands.Cog, name="Misc"):
    def __init__(self, bot: commands.Bot):
        super().__init__()  # init RemindMixin
        self.bot = bot
        self._rep_seen_edge: Set[tuple[int, int]] = set()  # (guild_id, user_id)

    # ---------- migrations ----------
    async def _migrate_seen_table(self):
        # Add columns if missing (safe to run multiple times)
        try:
            await self.bot.db.execute("ALTER TABLE seen ADD COLUMN last_msg_channel_id INTEGER")
        except Exception:
            pass
        try:
            await self.bot.db.execute("ALTER TABLE seen ADD COLUMN last_msg_id INTEGER")
        except Exception:
            pass

    # ---------- DB bootstrap ----------
    async def cog_load(self):
        # reminders table (unused by in-memory mixin but kept from original schema)
        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER NOT NULL,
            channel_id INTEGER,
            text TEXT NOT NULL,
            fire_at TEXT NOT NULL
        )
        """)
        await self.bot.db.execute("""CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(fire_at)""")

        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS afk (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT,
            set_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """)

        # Seen table with new columns (for fresh installs)
        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_seen TEXT NOT NULL,
            last_msg_channel_id INTEGER,
            last_msg_id INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
        """)
        # Also run migration to cover old DBs
        await self._migrate_seen_table()

        # New: greet / autoresponder / rep config
        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS greet_config (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message TEXT
        )
        """)
        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS autoresponses (
            guild_id INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            response TEXT NOT NULL,
            PRIMARY KEY (guild_id, trigger)
        )
        """)
        await self.bot.db.execute("""
        CREATE TABLE IF NOT EXISTS rep_config (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            vanity_code TEXT NOT NULL
        )
        """)

        # Best-effort backfill
        for g in self.bot.guilds:
            for m in g.members:
                try:
                    await self.bot.db.execute(
                        "INSERT OR IGNORE INTO seen(guild_id,user_id,last_seen) VALUES(?,?,?)",
                        g.id, m.id, ISO(datetime.now(timezone.utc))
                    )
                except Exception:
                    pass

    # ---------- listeners ----------
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if not msg.guild or not isinstance(msg.author, discord.Member):
            return

        # update SEEN with message link info (JIT migrate just in case)
        try:
            await self._migrate_seen_table()
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO seen(guild_id, user_id, last_seen, last_msg_channel_id, last_msg_id) "
                "VALUES(?, ?, ?, ?, ?)",
                msg.guild.id, msg.author.id,
                ISO(datetime.now(timezone.utc)),
                msg.channel.id, msg.id
            )
        except Exception:
            pass

        # Clear AFK when user talks
        row = await self.bot.db.fetchrow(
            "SELECT reason FROM afk WHERE guild_id=? AND user_id=?",
            msg.guild.id, msg.author.id
        )
        if row:
            await self.bot.db.execute(
                "DELETE FROM afk WHERE guild_id=? AND user_id=?",
                msg.guild.id, msg.author.id
            )
            try:
                await msg.channel.send(f"welcome back **{msg.author.name}**, removed your AFK.")
            except Exception:
                pass

        # Notify when mentioning AFK users
        if msg.mentions:
            for u in msg.mentions:
                r = await self.bot.db.fetchrow(
                    "SELECT reason, set_at FROM afk WHERE guild_id=? AND user_id=?",
                    msg.guild.id, u.id
                )
                if r:
                    try:
                        since = _fmt_dt(datetime.fromisoformat(r["set_at"]))
                    except Exception:
                        since = "earlier"
                    reason = r["reason"] or "AFK"
                    try:
                        await msg.channel.send(f"**{u.name}** is AFK ({reason}) — set {since}.")
                    except Exception:
                        pass

        # Auto-responder (case-insensitive "contains")
        try:
            ars = await self.bot.db.fetch(
                "SELECT trigger, response FROM autoresponses WHERE guild_id=?",
                msg.guild.id
            )
            if ars:
                content = msg.content.lower()
                for r in ars:
                    trig = (r["trigger"] or "").lower().strip()
                    if trig and trig in content:
                        try:
                            await msg.channel.send(r["response"])
                        except Exception:
                            pass
                        break  # only fire the first match
        except Exception:
            pass

    # Greet on join
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            row = await self.bot.db.fetchrow(
                "SELECT channel_id, message FROM greet_config WHERE guild_id=?",
                member.guild.id
            )
            if not row:
                return
            ch = member.guild.get_channel(row["channel_id"])
            if not isinstance(ch, (discord.TextChannel, discord.Thread)):
                return
            msg = (row["message"] or "").strip()
            if msg:
                _m = await ch.send(f"{member.mention} {msg}")
                try:
                    await _m.delete(delay=5)
                except Exception:
                    pass
            else:
                _m = await ch.send(f"{member.mention}")
                try:
                    await _m.delete(delay=5)
                except Exception:
                    pass
        except Exception:
            pass

    # Presence watcher for vanity rep (custom status only; bios are not accessible to bots)
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        # Ignore users who are offline/invisible
        try:
            if after.status in (discord.Status.offline, discord.Status.invisible):
                return
        except Exception:
            pass

        try:
            cfg = await self.bot.db.fetchrow(
                "SELECT channel_id, role_id, vanity_code FROM rep_config WHERE guild_id=?",
                after.guild.id
            )
            if not cfg:
                return

            channel = after.guild.get_channel(cfg["channel_id"])
            role = after.guild.get_role(cfg["role_id"])
            vanity_code = (cfg["vanity_code"] or "").strip()
            if not (channel and role and vanity_code):
                return

            # Build regex and compute before/after matches
            pat = _compile_vanity_regex(vanity_code)
            before_txt = _member_custom_status_text(before)
            after_txt = _member_custom_status_text(after)
            had = bool(pat.search(before_txt or ""))
            has = bool(pat.search(after_txt or ""))

            # Positive edge: just added vanity in status (e.g., offline->online with vanity now visible)
            if has and not had:
                if role not in after.roles:
                    try:
                        await after.add_roles(role, reason="Vanity rep detected in custom status")
                    except (discord.Forbidden, discord.HTTPException):
                        return

                    # Announce with cooldown (prevents mass pings)
                    try:
                        import time
                        key = (after.guild.id, after.id)
                        last = getattr(self, "_rep_last_announce", {}).get(key, 0)
                        now = time.time()
                        if now - last >= 600:  # 10 minutes
                            try:
                                await channel.send(f"**{after.name}** has repped our vanity and gained **30 seconds** of claimtime!!")
                            except Exception:
                                pass
                            try:
                                self._rep_last_announce[key] = now
                            except Exception:
                                pass
                    except Exception:
                        pass

            # Negative edge: they removed vanity from status (do NOT include offline status)
            elif had and not has:
                try:
                    # If user is offline/invisible we ignore, but we already returned above.
                    if role in after.roles:
                        try:
                            await after.remove_roles(role, reason="Vanity rep removed from custom status")
                        except (discord.Forbidden, discord.HTTPException):
                            return
                except Exception:
                    pass

            # Else: no change relevant to our vanity; do nothing
        except Exception:
            pass

    @commands.command(name="greet")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def greet(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        *,
        message: Optional[str] = None
    ):
        """
        Toggle or configure greeting.
        - No args: toggle on/off (on = current channel, no custom text)
        - With args: enable/update config to the given channel/message
        The actual greet will auto-delete after 5 seconds on member join (handled in your listener).
        """
        guild_id = ctx.guild.id

        # Check current state
        row = await self.bot.db.fetchrow(
            "SELECT channel_id, message FROM greet_config WHERE guild_id = ?",
            guild_id
        )

        # TOGGLE mode (no args)
        if channel is None and message is None:
            if row:
                # Currently ON → turn OFF
                await self.bot.db.execute(
                    "DELETE FROM greet_config WHERE guild_id = ?",
                    guild_id
                )
                return await ctx.reply("🧹 Greeting disabled. I won't ping new members.")
            else:
                # Currently OFF → turn ON with defaults (this channel, no custom text)
                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO greet_config(guild_id, channel_id, message) VALUES(?,?,?)",
                    guild_id, ctx.channel.id, ""
                )
                return await ctx.reply(
                    f"✅ Greeting enabled. On join I’ll post in {ctx.channel.mention}: `@member` "
                    "(auto-deletes after 5s)"
                )

        # Configure mode (args provided)
        ch = channel or ctx.channel
        msg = (message or "").strip()

        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO greet_config(guild_id, channel_id, message) VALUES(?,?,?)",
                guild_id, ch.id, msg
            )
        except Exception as e:
            return await ctx.reply(f"❌ Couldn't set greet: {e}")

        if msg:
            await ctx.reply(
                f"✅ Greeting set. On join I’ll post in {ch.mention}: `@member {msg}` "
                "(auto-deletes after 5s)"
            )
        else:
            await ctx.reply(
                f"✅ Greeting set. On join I’ll post in {ch.mention}: just `@member` "
                "(auto-deletes after 5s)"
            )


    # ,ar <trigger> <response...>
    @commands.command(name="ar", usage="<trigger> <response...>")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def ar(self, ctx: commands.Context, trigger: Optional[str] = None, *, response: Optional[str] = None):
        if not trigger or not response:
            return await ctx.reply("Usage: `,ar <trigger> <response>`")
        trig = trigger.strip()
        resp = response.strip()
        if not trig or not resp:
            return await ctx.reply("Usage: `,ar <trigger> <response>`")
        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO autoresponses(guild_id, trigger, response) VALUES(?,?,?)",
                ctx.guild.id, trig, resp
            )
            await ctx.reply(f"✅ Auto-response added: when a message contains **{trig}**, I’ll reply with:\n> {resp}")
        except Exception as e:
            await ctx.reply(f"❌ Couldn't add auto-response: {e}")

    # ,rep #channel [@role]
    @commands.command(name="rep")
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def rep(self, ctx: commands.Context, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        """
        Configure the rep system using the guild's Vanity URL code (Server Settings).
        - channel: where to announce the rep grant
        - role (optional): role to grant; if missing, creates/uses 'Vanity Rep'
        Detection: /vanity, .gg/vanity, discord.gg/vanity (http/https ok). Bare 'vanity' does NOT match.
        Note: Bots cannot read user bios; this watches CUSTOM STATUS only.
        """
        code = ctx.guild.vanity_url_code
        if not code:
            return await ctx.reply("❌ This server doesn't have a Vanity URL set in Server Settings.")

        rep_role = role
        if rep_role is None:
            rep_role = discord.utils.get(ctx.guild.roles, name="Vanity Rep")
            if rep_role is None:
                try:
                    rep_role = await ctx.guild.create_role(name="Vanity Rep", reason="Rep system auto-role")
                except discord.Forbidden:
                    return await ctx.reply("❌ I need **Manage Roles** permission to create/grant roles.")
                except discord.HTTPException as e:
                    return await ctx.reply(f"❌ Couldn't create role: {e}")

        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO rep_config(guild_id, channel_id, role_id, vanity_code) VALUES(?,?,?,?)",
                ctx.guild.id, channel.id, rep_role.id, code
            )
            await ctx.reply(
                f"✅ Rep system set.\n"
                f"- Announce in: {channel.mention}\n"
                f"- Role: {rep_role.mention}\n"
                f"- Vanity code: `{code}`\n\n"
                f"Members who put `/`/`.gg/`/`discord.gg/` with `{code}` in their **custom status** will get {rep_role.mention}."
            )
        except Exception as e:
            await ctx.reply(f"❌ Couldn't set rep system: {e}")

    # ---------- emoji ----------
    @commands.group(name="emoji", invoke_without_command=True)
    @commands.guild_only()
    async def emoji_group(self, ctx: commands.Context):
        await ctx.reply("Usage: `,emoji add <emoji> <emoji2> ...`")

    @emoji_group.command(name="add")
    @commands.guild_only()
    async def emoji_add(self, ctx: commands.Context, *emoji_tokens: str):
        """
        ,emoji add <emoji> <emoji2> ...
        Accepts custom emoji mentions like <:name:id> or <a:name:id>.
        """
        if not emoji_tokens:
            return await ctx.reply("Give me at least one custom emoji: `,emoji add <:foo:123> <:bar:456>`")

        # Permission checks (author + bot)
        if not _has_manage_expressions(ctx.author.guild_permissions):
            return await ctx.reply("You need **Manage Emojis & Stickers** to do that.")
        me_perms = ctx.guild.me.guild_permissions
        if not _has_manage_expressions(me_perms):
            return await ctx.reply("I need **Manage Emojis & Stickers** to add emojis here.")

        created: List[discord.Emoji] = []
        failures: List[Tuple[str, str]] = []

        for token in emoji_tokens:
            m = EMOJI_MENTION_RE.fullmatch(token)
            if not m:
                failures.append((token, "not a custom emoji mention"))
                continue

            animated = bool(m.group(1))
            base_name = _sanitize_name(m.group(2))
            emoji_id = int(m.group(3))
            pe = discord.PartialEmoji(animated=animated, name=base_name, id=emoji_id)

            # fetch bytes
            data = await _fetch_bytes(pe)
            if not data:
                failures.append((token, "couldn't fetch emoji image"))
                continue

            if len(data) > 256_000:
                failures.append((token, "image > 256 KB"))
                continue

            # try create; try alt names on conflict/HTTP error
            tried = [base_name, f"{base_name}_1", f"{base_name}_2", f"{base_name}_3"]
            created_emoji: Optional[discord.Emoji] = None
            last_err: Optional[Exception] = None

            for candidate in tried:
                try:
                    created_emoji = await ctx.guild.create_custom_emoji(
                        name=candidate, image=data, reason=f"Imported by {ctx.author} via ,emoji add"
                    )
                    break
                except discord.Forbidden as e:
                    last_err = e
                    break
                except discord.HTTPException as e:
                    last_err = e
                    continue

            if created_emoji:
                created.append(created_emoji)
            else:
                reason = "creation failed"
                if isinstance(last_err, discord.Forbidden):
                    reason = "insufficient permissions"
                elif isinstance(last_err, discord.HTTPException):
                    reason = f"HTTP {last_err.status}"
                failures.append((token, reason))

        if created:
            added_str = " ".join(str(e) for e in created)
            await ctx.reply(f"✅ Added {len(created)} emoji: {added_str}.")
        else:
            await ctx.reply("No emojis were added.")

        if failures:
            details = "\n".join(f"• {tok}: {why}" for tok, why in failures)
            await ctx.send(f"Some failed:\n{details}")

    # ---------- bot info ----------
    @commands.command(name="bot", aliases=["info", "botinfo", "about", "stats"])
    async def abt(self, ctx: commands.Context):
        ping = round(self.bot.latency * 1000)
        e = discord.Embed(
            title=f"{BOT_DISPLAY_NAME}",
            description=f"Made by **{BOT_MADE_BY}**",
            color=0x2b2d31
        )
        e.add_field(name="Latency", value=f"{ping} ms")
        e.add_field(name="Guilds", value=str(len(self.bot.guilds)))
        users = sum(g.member_count or 0 for g in self.bot.guilds)
        e.add_field(name="Users", value=str(users))
        await ctx.send(embed=e)

    # ---------- define ----------
    @commands.command(name="define", usage="<word>")
    async def define(self, ctx: commands.Context, *, word: str):
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote_plus(word)}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return await ctx.send("no definition found.")
                    data = await r.json()
            meanings = data[0].get("meanings", []) if isinstance(data, list) and data else []
            if not meanings:
                return await ctx.send("no definition found.")
            lines = []
            for m in meanings[:2]:
                part = m.get("partOfSpeech", "")
                defs = m.get("definitions", [])
                for d in defs[:2]:
                    defi = d.get("definition", "")
                    if defi:
                        lines.append(f"**{part}**: {defi}")
            if not lines:
                return await ctx.send("no definition found.")
            await ctx.send("\n".join(lines))
        except Exception:
            await ctx.send("no definition found.")

    # ---------- bots ----------
    @commands.command(name="bots")
    async def bots(self, ctx: commands.Context):
        bs = [m for m in ctx.guild.members if m.bot]
        if not bs:
            return await ctx.send("no bots.")
        await ctx.send(", ".join(m.name for m in bs))

    # ---------- seen (mentions + clickable last message) ----------
    @commands.command(name="seen", usage="[@user]")
    async def seen(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author

        async def _fetch():
            return await self.bot.db.fetchrow(
                "SELECT last_seen, last_msg_channel_id, last_msg_id FROM seen WHERE guild_id=? AND user_id=?",
                ctx.guild.id, member.id
            )

        row = None
        try:
            row = await _fetch()
        except Exception:
            await self._migrate_seen_table()
            row = await _fetch()

        if not row:
            return await ctx.send("no data.")

        d = dict(row)
        try:
            dt = datetime.fromisoformat(d["last_seen"])
        except Exception:
            return await ctx.send("no data.")

        ch_id = d.get("last_msg_channel_id")
        msg_id = d.get("last_msg_id")
        link = f"https://discord.com/channels/{ctx.guild.id}/{ch_id}/{msg_id}" if ch_id and msg_id else None

        text = f"{member.mention} was last seen {_fmt_dt(dt)}"
        if link:
            text += f" — [last message]({link})"
        await ctx.send(text)

    # ---------- avatars/banners ----------
    @commands.command(name="avatar", aliases=["av"], usage="[@user]")
    async def avatar(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        await ctx.send(member.display_avatar.url)

    @commands.command(name="banner", usage="[@user]")
    async def banner(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        try:
            user = await self.bot.fetch_user(member.id)
            if user.banner:
                return await ctx.send(user.banner.url)
        except Exception:
            pass
        await ctx.send("no banner.")

    @commands.command(name="serveravatar", usage="[@user]")
    async def serveravatar(self, ctx: commands.Context):
        icon = ctx.guild.icon
        if icon:
            await ctx.send(icon.url)
        else:
            await ctx.send("no server avatar.")

    @commands.command(name="serverbanner")
    async def serverbanner(self, ctx: commands.Context):
        if ctx.guild.banner:
            return await ctx.send(ctx.guild.banner.url)
        await ctx.send("no server banner.")

    # ---------- choose ----------
    @commands.command(name="choose", usage="<choice1 | choice2 | ...>")
    async def choose(self, ctx: commands.Context, *, choices: str):
        parts = [p.strip() for p in re.split(r"[|,]", choices) if p.strip()]
        if len(parts) < 2:
            return await ctx.send("provide 2 or more choices separated by `|` or `,`.")
        await ctx.send(random.choice(parts))

    # ---------- wikihow ----------
    @commands.command(name="wikihow", usage="<query>")
    async def wikihow(self, ctx: commands.Context, *, query: str):
        url = f"https://www.wikihow.com/wikiHowTo?search={quote_plus(query)}"
        await ctx.send(url)

    # ---------- make MP3 (ffmpeg required) ----------
    @commands.command(name="makemp3", usage="[url] (or attach a file)")
    async def makemp3(self, ctx: commands.Context, url: Optional[str] = None):
        src_url = None
        if ctx.message.attachments:
            src_url = ctx.message.attachments[0].url
        elif url:
            src_url = url
        if not src_url:
            return await ctx.send("attach a media file or pass a direct URL.")
        tmp_in = f"tmp_{ctx.author.id}_{ctx.message.id}.input"
        tmp_out = f"tmp_{ctx.author.id}_{ctx.message.id}.mp3"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(src_url) as r:
                    if r.status != 200:
                        return await ctx.send("failed to download source.")
                    data = await r.read()
            with open(tmp_in, "wb") as f:
                f.write(data)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", tmp_in, "-vn", "-acodec", "libmp3lame", "-q:a", "2", tmp_out,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            if not os.path.exists(tmp_out):
                return await ctx.send("conversion failed.")
            await ctx.send(file=discord.File(tmp_out, filename="audio.mp3"))
        except Exception:
            await ctx.send("failed.")
        finally:
            for p in (tmp_in, tmp_out):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

# --- extension setup ---
async def setup(bot: commands.Bot):
    # prevent duplicate global ',remind' if another cog defined it
    try:
        if bot.get_command("remind"):
            bot.remove_command("remind")
    except Exception:
        pass
    await bot.add_cog(Misc(bot))
