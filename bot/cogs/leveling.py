# bot/cogs/leveling.py
from __future__ import annotations

import asyncio
import collections
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Deque, Dict, Tuple

import discord
from discord.ext import commands

# =============== SQLite schema (with safe migrations) ===============

TABLE_XP = """
CREATE TABLE IF NOT EXISTS xp (
  guild_id    INTEGER NOT NULL,
  user_id     INTEGER NOT NULL,
  xp          INTEGER NOT NULL DEFAULT 0,
  level       INTEGER NOT NULL DEFAULT 0,
  last_award_at   TEXT,
  suspend_until   TEXT,                  -- ISO time until which XP is disabled
  strikes         INTEGER NOT NULL DEFAULT 0,
  opt_out         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id)
)
"""

TABLE_SETTINGS = """
CREATE TABLE IF NOT EXISTS xp_settings (
  guild_id            INTEGER PRIMARY KEY,
  locked              INTEGER NOT NULL DEFAULT 0,
  log_channel_id      INTEGER,
  gain_min            INTEGER NOT NULL DEFAULT 8,
  gain_max            INTEGER NOT NULL DEFAULT 14,
  cooldown_s          INTEGER NOT NULL DEFAULT 8,
  short_window_s      INTEGER NOT NULL DEFAULT 5,
  short_threshold     INTEGER NOT NULL DEFAULT 7,
  long_window_s       INTEGER NOT NULL DEFAULT 30,
  long_threshold      INTEGER NOT NULL DEFAULT 20,
  min_chars           INTEGER NOT NULL DEFAULT 5,
  roles_highest_only  INTEGER NOT NULL DEFAULT 1   -- 1=keep highest only, 0=stack all
)
"""

TABLE_LEVEL_ROLES = """
CREATE TABLE IF NOT EXISTS xp_level_roles (
  guild_id  INTEGER NOT NULL,
  role_id   INTEGER NOT NULL,
  level     INTEGER NOT NULL,
  PRIMARY KEY (guild_id, role_id)
)
"""

TABLE_WHITELIST = """
CREATE TABLE IF NOT EXISTS xp_whitelist (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  PRIMARY KEY (guild_id, user_id)
)
"""

TABLE_IGNORE_CHANNELS = """
CREATE TABLE IF NOT EXISTS xp_ignore_channels (
  guild_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  PRIMARY KEY (guild_id, channel_id)
)
"""

TABLE_IGNORE_ROLES = """
CREATE TABLE IF NOT EXISTS xp_ignore_roles (
  guild_id INTEGER NOT NULL,
  role_id INTEGER NOT NULL,
  PRIMARY KEY (guild_id, role_id)
)
"""

# =============== Config / constants ===============

def level_required_xp(level: int) -> int:
    # Mildly harder curve; smooth progression
    return 25 * (level ** 2) + 150 * level + 120

DEFAULT_SHORT_WINDOW_S = 5
DEFAULT_SHORT_THRESHOLD = 7
DEFAULT_LONG_WINDOW_S = 30
DEFAULT_LONG_THRESHOLD = 20

FLAG_COOLDOWN_S = 60

SIG_WINDOW_S = 30
SIG_REPEAT_THRESHOLD = 3   # identical text >=3 times within SIG_WINDOW -> abuse
SIG_MAX_KEEP = 12          # last 12 signatures tracked in memory per user

SUSPEND_MINUTES = [0, 10, 60, 360]  # 0, 10m, 1h, 6h (4+ => 6h)

# =============== Utility helpers ===============

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def normalize_content(s: str) -> str:
    """Lowercase, strip markup/extra spaces; keep alnum + basic punctuation."""
    s = s.lower()
    s = re.sub(r"<a?:\w+:\d+>", "", s)      # custom emojis
    s = re.sub(r"<@!?&?\d+>", "", s)        # mentions
    s = re.sub(r"https?://\S+", " ", s)     # links
    s = re.sub(r"[^\w\s.,!?-]+", " ", s)    # keep basic punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s

def duration_to_seconds(s: str) -> Optional[int]:
    """Parse '1w2d3h4m5s' or plain seconds into total seconds."""
    s = s.strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0
    for num, unit in re.findall(r"(\d+)\s*([wdhms])", s):
        n = int(num)
        if unit == "w": total += n * 7 * 24 * 3600
        elif unit == "d": total += n * 24 * 3600
        elif unit == "h": total += n * 3600
        elif unit == "m": total += n * 60
        elif unit == "s": total += n
    return total or None

# =============== The Cog ===============

class Leveling(commands.Cog, name="Leveling"):
    """
    Seamless leveling with adaptive anti-abuse:
    - XP on legitimate messages (random gain, length bonus).
    - Sliding windows + content-similarity checks to detect spam.
    - Flags, rate-limit, and suspends XP on abuse; logs details.
    - Admin controls (lock, thresholds, gains, cooldown, roles, whitelist, ignore, …).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # in-memory windows: (guild_id, user_id) -> deque[timestamps]
        self._windows: Dict[Tuple[int, int], Deque[datetime]] = {}
        # last flag time to avoid log spam
        self._last_flag: Dict[Tuple[int, int], datetime] = {}
        # recent content signatures: (guild_id, user_id) -> deque[(ts, sig)]
        self._sigs: Dict[Tuple[int, int], Deque[Tuple[datetime, str]]] = {}

    # -------------------- lifecycle / migrations --------------------

    async def cog_load(self):
        await self.bot.db.execute(TABLE_XP)
        await self.bot.db.execute(TABLE_SETTINGS)
        await self.bot.db.execute(TABLE_LEVEL_ROLES)
        await self.bot.db.execute(TABLE_WHITELIST)
        await self.bot.db.execute(TABLE_IGNORE_CHANNELS)
        await self.bot.db.execute(TABLE_IGNORE_ROLES)

        # safe migrations (ignore if already added)
        for sql in (
            "ALTER TABLE xp ADD COLUMN suspend_until TEXT",
            "ALTER TABLE xp ADD COLUMN strikes INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE xp ADD COLUMN opt_out INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE xp_settings ADD COLUMN log_channel_id INTEGER",
            f"ALTER TABLE xp_settings ADD COLUMN gain_min INTEGER NOT NULL DEFAULT {8}",
            f"ALTER TABLE xp_settings ADD COLUMN gain_max INTEGER NOT NULL DEFAULT {14}",
            f"ALTER TABLE xp_settings ADD COLUMN cooldown_s INTEGER NOT NULL DEFAULT {8}",
            f"ALTER TABLE xp_settings ADD COLUMN short_window_s INTEGER NOT NULL DEFAULT {DEFAULT_SHORT_WINDOW_S}",
            f"ALTER TABLE xp_settings ADD COLUMN short_threshold INTEGER NOT NULL DEFAULT {DEFAULT_SHORT_THRESHOLD}",
            f"ALTER TABLE xp_settings ADD COLUMN long_window_s INTEGER NOT NULL DEFAULT {DEFAULT_LONG_WINDOW_S}",
            f"ALTER TABLE xp_settings ADD COLUMN long_threshold INTEGER NOT NULL DEFAULT {DEFAULT_LONG_THRESHOLD}",
            "ALTER TABLE xp_settings ADD COLUMN min_chars INTEGER NOT NULL DEFAULT 5",
            "ALTER TABLE xp_settings ADD COLUMN roles_highest_only INTEGER NOT NULL DEFAULT 1",
        ):
            try:
                await self.bot.db.execute(sql)
            except Exception:
                pass  # benign (already exists, etc.)

    # -------------------- internal helpers --------------------

    async def _settings(self, guild_id: int) -> dict:
        row = await self.bot.db.fetchrow("SELECT * FROM xp_settings WHERE guild_id=?", guild_id)
        if not row:
            await self.bot.db.execute("INSERT INTO xp_settings(guild_id) VALUES(?)", guild_id)
            row = await self.bot.db.fetchrow("SELECT * FROM xp_settings WHERE guild_id=?", guild_id)
        return dict(row)

    async def _is_locked(self, guild_id: int) -> bool:
        s = await self._settings(guild_id)
        return bool(int(s["locked"]) == 1)

    async def _is_whitelisted(self, guild_id: int, user_id: int) -> bool:
        row = await self.bot.db.fetchrow(
            "SELECT 1 FROM xp_whitelist WHERE guild_id=? AND user_id=?",
            guild_id, user_id
        )
        return bool(row)

    async def _ignored_channel(self, guild_id: int, channel_id: int) -> bool:
        row = await self.bot.db.fetchrow(
            "SELECT 1 FROM xp_ignore_channels WHERE guild_id=? AND channel_id=?", guild_id, channel_id
        )
        return bool(row)

    async def _ignored_roles(self, guild_id: int) -> List[int]:
        rows = await self.bot.db.fetchall(
            "SELECT role_id FROM xp_ignore_roles WHERE guild_id=?", guild_id
        )
        return [int(r["role_id"]) for r in rows] if rows else []

    async def _get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        s = await self._settings(guild.id)
        chan = guild.get_channel(s.get("log_channel_id") or 0)
        if isinstance(chan, discord.TextChannel):
            return chan
        existing = discord.utils.get(guild.text_channels, name="leveling-logs")
        if existing:
            await self.bot.db.execute(
                "UPDATE xp_settings SET log_channel_id=? WHERE guild_id=?",
                existing.id, guild.id
            )
            return existing
        try:
            created = await guild.create_text_channel("leveling-logs", reason="Leveling logs")
            await self.bot.db.execute(
                "UPDATE xp_settings SET log_channel_id=? WHERE guild_id=?",
                created.id, guild.id
            )
            return created
        except Exception:
            return None

    def _push_windows(self, guild_id: int, user_id: int, short_s: int, long_s: int) -> Tuple[int, int]:
        now = utcnow()
        key = (guild_id, user_id)
        dq = self._windows.get(key)
        if dq is None:
            dq = collections.deque()
            self._windows[key] = dq
        dq.append(now)
        long_cut = now - timedelta(seconds=long_s)
        while dq and dq[0] < long_cut:
            dq.popleft()
        short_cut = now - timedelta(seconds=short_s)
        cnt_short = 0
        for t in reversed(dq):
            if t >= short_cut:
                cnt_short += 1
            else:
                break
        return cnt_short, len(dq)

    def _push_signature(self, guild_id: int, user_id: int, content: str) -> int:
        sig = normalize_content(content)
        now = utcnow()
        key = (guild_id, user_id)
        dq = self._sigs.get(key)
        if dq is None:
            dq = collections.deque()
            self._sigs[key] = dq
        dq.append((now, sig))
        cut = now - timedelta(seconds=SIG_WINDOW_S)
        while dq and dq[0][0] < cut:
            dq.popleft()
        same = sum(1 for ts, s in dq if s == sig)
        while len(dq) > SIG_MAX_KEEP:
            dq.popleft()
        return same

    async def _flag_and_escalate(self, message: discord.Message, reason: str, settings: dict):
        key = (message.guild.id, message.author.id)
        now = utcnow()
        last = self._last_flag.get(key)
        if last and (now - last).total_seconds() < FLAG_COOLDOWN_S:
            return
        self._last_flag[key] = now

        row = await self.bot.db.fetchrow(
            "SELECT strikes FROM xp WHERE guild_id=? AND user_id=?",
            message.guild.id, message.author.id
        )
        strikes = int(row["strikes"]) + 1 if row else 1
        idx = min(strikes, len(SUSPEND_MINUTES) - 1)
        minutes = SUSPEND_MINUTES[idx]
        suspend_until_iso = None
        if minutes > 0:
            until = now + timedelta(minutes=minutes)
            suspend_until_iso = iso(until)

        existing = await self.bot.db.fetchrow(
            "SELECT xp, level, last_award_at, suspend_until, opt_out FROM xp WHERE guild_id=? AND user_id=?",
            message.guild.id, message.author.id
        )
        xp = int(existing["xp"]) if existing else 0
        lvl = int(existing["level"]) if existing else 0
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,?)",
            message.guild.id, message.author.id, xp, lvl,
            existing["last_award_at"] if existing else None,
            suspend_until_iso if suspend_until_iso else (existing["suspend_until"] if existing else None),
            strikes,
            int(existing["opt_out"]) if existing else 0
        )

        ch = await self._get_log_channel(message.guild)
        if isinstance(ch, discord.TextChannel):
            try:
                susp = f" • Suspended {minutes}m" if minutes > 0 else " • Warn only"
                await ch.send(
                    f"[leveling-flag] {message.author.mention} — {reason}{susp} • "
                    f"Strikes: **{strikes}** • Channel: {message.channel.mention}"
                )
            except Exception:
                pass

    # -------------------- listener --------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Award XP to legit messages; anti-abuse will rate-limit/suspend."""
        if not message.guild or message.author.bot:
            return

        guild_id = message.guild.id
        user_id = message.author.id

        # global lock?
        if await self._is_locked(guild_id):
            return

        # ignored channels / roles
        if await self._ignored_channel(guild_id, message.channel.id):
            return
        ignore_roles = set(await self._ignored_roles(guild_id))
        if ignore_roles and isinstance(message.author, discord.Member):
            if any((r.id in ignore_roles) for r in message.author.roles):
                return

        # settings
        s = await self._settings(guild_id)
        gain_min = int(s["gain_min"])
        gain_max = int(s["gain_max"])
        base_cooldown = int(s["cooldown_s"])
        short_s = int(s["short_window_s"])
        long_s = int(s["long_window_s"])
        short_thr = int(s["short_threshold"])
        long_thr = int(s["long_threshold"])
        min_chars = int(s["min_chars"])

        # fetch user row
        row = await self.bot.db.fetchrow(
            "SELECT xp, level, last_award_at, suspend_until, strikes, opt_out "
            "FROM xp WHERE guild_id=? AND user_id=?",
            guild_id, user_id
        )
        if row and int(row["opt_out"]) == 1:
            return

        is_whitelisted = await self._is_whitelisted(guild_id, user_id)

        # suspension
        suspend_until = parse_iso(row["suspend_until"]) if row else None
        if suspend_until and utcnow() < suspend_until:
            cnt_s, cnt_l = self._push_windows(guild_id, user_id, short_s, long_s)
            rep = self._push_signature(guild_id, user_id, message.content or "")
            if not is_whitelisted and (cnt_s >= short_thr or cnt_l >= long_thr or rep >= SIG_REPEAT_THRESHOLD):
                reason = f"{cnt_s}/{short_s}s | {cnt_l}/{long_s}s | repeats={rep}"
                await self._flag_and_escalate(message, reason, s)
            return

        # eligibility
        content = (message.content or "").strip()
        normalized = normalize_content(content)
        has_media = bool(message.attachments or message.stickers)
        eligible = has_media or len(normalized) >= min_chars
        if not eligible:
            cnt_s, cnt_l = self._push_windows(guild_id, user_id, short_s, long_s)
            rep = self._push_signature(guild_id, user_id, content)
            if not is_whitelisted and (cnt_s >= short_thr or cnt_l >= long_thr or rep >= SIG_REPEAT_THRESHOLD):
                reason = f"tiny msgs — {cnt_s}/{short_s}s | {cnt_l}/{long_s}s | repeats={rep}"
                await self._flag_and_escalate(message, reason, s)
            return

        # cooldown (adaptive)
        last_award_at = parse_iso(row["last_award_at"]) if row else None
        cooldown = base_cooldown
        cnt_s, cnt_l = self._push_windows(guild_id, user_id, short_s, long_s)
        if cnt_l > (long_thr // 2):
            cooldown = max(base_cooldown, base_cooldown + 4)

        if last_award_at and (utcnow() - last_award_at).total_seconds() < cooldown:
            rep = self._push_signature(guild_id, user_id, content)
            if not is_whitelisted and (cnt_s >= short_thr or cnt_l >= long_thr or rep >= SIG_REPEAT_THRESHOLD):
                reason = f"cooling — {cnt_s}/{short_s}s | {cnt_l}/{long_s}s | repeats={rep}"
                await self._flag_and_escalate(message, reason, s)
            return

        # repetition/abuse gates
        rep = self._push_signature(guild_id, user_id, content)
        if not is_whitelisted and rep >= SIG_REPEAT_THRESHOLD:
            reason = f"repeated content {rep}× in {SIG_WINDOW_S}s"
            await self._flag_and_escalate(message, reason, s)
            return
        if not is_whitelisted and (cnt_s >= short_thr or cnt_l >= long_thr):
            reason = f"{cnt_s}/{short_s}s | {cnt_l}/{long_s}s"
            await self._flag_and_escalate(message, reason, s)
            return

        # ----- Award XP -----
        cur_xp = int(row["xp"]) if row else 0
        level = int(row["level"]) if row else 0

        # random gain + small length bonus
        gain = random.randint(gain_min, max(gain_min, gain_max))
        if len(normalized) >= 160:
            gain += 4
        elif len(normalized) >= 80:
            gain += 2

        cur_xp += gain
        leveled = False
        while cur_xp >= level_required_xp(level):
            level += 1
            leveled = True

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,?)",
            guild_id, user_id, cur_xp, level, iso(utcnow()),
            row["suspend_until"] if row else None,
            int(row["strikes"]) if row else 0,
            int(row["opt_out"]) if row else 0
        )

        if leveled:
            try:
                await message.author.send(
                    f"🎉 **Level up!** You reached **Level {level}** in **{message.guild.name}**."
                )
            except Exception:
                pass
            await self._grant_level_roles(message.author, level)

    # -------------------- roles helper --------------------

    async def _grant_level_roles(self, member: discord.Member, new_level: int):
        rows = await self.bot.db.fetchall(
            "SELECT role_id, level FROM xp_level_roles WHERE guild_id=? ORDER BY level ASC",
            member.guild.id
        )
        if not rows:
            return

        s = await self._settings(member.guild.id)
        highest_only = bool(int(s.get("roles_highest_only", 1)) == 1)

        # Collect eligible roles
        eligible: List[Tuple[int, int]] = []  # (level, role_id)
        me = member.guild.me
        for r in rows:
            role_id = int(r["role_id"])
            need = int(r["level"])
            role = member.guild.get_role(role_id)
            if not role:
                continue
            if role >= me.top_role:
                continue
            if new_level >= need:
                eligible.append((need, role_id))

        if not eligible:
            return

        if highest_only:
            # Give highest eligible; remove all other configured level roles the member has
            need, rid = max(eligible, key=lambda x: x[0])
            role = member.guild.get_role(rid)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"level {new_level} reached")
                except Exception:
                    pass
            # remove all other level roles from this set
            configured_ids = {int(r["role_id"]) for r in rows}
            to_remove = [member.guild.get_role(rid) for rid in configured_ids if rid != role.id]
            to_remove = [r for r in to_remove if r and r in member.roles and r < me.top_role]
            if to_remove:
                try:
                    await member.remove_roles(*to_remove, reason="keep highest level role only")
                except Exception:
                    pass
        else:
            # Stack: add all eligible missing roles, keep old ones
            to_add = []
            for need, rid in eligible:
                role = member.guild.get_role(rid)
                if role and role not in member.roles:
                    to_add.append(role)
            if to_add:
                try:
                    await member.add_roles(*to_add, reason=f"level {new_level} reached")
                except Exception:
                    pass

    # -------------------- public commands --------------------

    @commands.command(name="rank", usage="[@user]")
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        m = member or ctx.author
        row = await self.bot.db.fetchrow(
            "SELECT xp, level FROM xp WHERE guild_id=? AND user_id=?",
            ctx.guild.id, m.id
        )
        if not row:
            return await ctx.send(f"**{m.mention}** has no XP yet.")
        await ctx.send(f"**{m.mention}** — Level **{row['level']}** | **{row['xp']} XP**")

    @commands.command(name="top")
    async def top(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall(
            "SELECT user_id, xp, level FROM xp WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
            ctx.guild.id
        )
        if not rows:
            return await ctx.send("no data.")
        embed = discord.Embed(title=f"Leaderboard — {ctx.guild.name}", color=0x2b2d31)
        desc_lines: List[str] = []
        for i, r in enumerate(rows, 1):
            user = ctx.guild.get_member(r["user_id"]) or f"<@{r['user_id']}>"
            name = user.mention if isinstance(user, discord.Member) else str(user)
            desc_lines.append(f"**{i}. {name}** — L{r['level']} ({r['xp']} XP)")
        embed.description = "\n".join(desc_lines)
        await ctx.send(embed=embed)

    # ----- Admin group -----

    @commands.group(name="level", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def level_group(self, ctx: commands.Context):
        s = await self._settings(ctx.guild.id)
        locked = "locked" if int(s["locked"]) == 1 else "unlocked"
        mode = "highest" if int(s.get("roles_highest_only", 1)) == 1 else "stack"
        await ctx.send(
            "**Leveling config**\n"
            f"• Status: **{locked}**\n"
            f"• Log: {('<#'+str(s['log_channel_id'])+'>' if s.get('log_channel_id') else 'auto')}\n"
            f"• Gain: **{s['gain_min']}–{s['gain_max']}** XP, cooldown **{s['cooldown_s']}s**\n"
            f"• Windows: **{s['short_threshold']} in {s['short_window_s']}s**, "
            f"**{s['long_threshold']} in {s['long_window_s']}s**\n"
            f"• Min chars: **{s['min_chars']}**\n"
            f"• Role mode: **{mode}**"
        )

    @level_group.command(name="lock")
    @commands.has_permissions(manage_guild=True)
    async def level_lock(self, ctx: commands.Context):
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, locked) VALUES(?,1) "
            "ON CONFLICT(guild_id) DO UPDATE SET locked=1",
            ctx.guild.id
        )
        await ctx.send("🔒 Leveling **locked**.")

    @level_group.command(name="unlock")
    @commands.has_permissions(manage_guild=True)
    async def level_unlock(self, ctx: commands.Context):
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, locked) VALUES(?,0) "
            "ON CONFLICT(guild_id) DO UPDATE SET locked=0",
            ctx.guild.id
        )
        await ctx.send("🔓 Leveling **unlocked**.")

    @commands.command(name="levellock")
    @commands.has_permissions(manage_guild=True)
    async def levellock_alias(self, ctx: commands.Context):
        await self.level_lock.callback(self, ctx)

    @commands.command(name="levelunlock")
    @commands.has_permissions(manage_guild=True)
    async def levelunlock_alias(self, ctx: commands.Context):
        await self.level_unlock.callback(self, ctx)

    # Log channel
    @level_group.command(name="log")
    @commands.has_permissions(manage_guild=True)
    async def level_log(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, log_channel_id) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=excluded.log_channel_id",
            ctx.guild.id, channel.id
        )
        await ctx.send(f"📝 Log channel set to {channel.mention}")

    # Gains and cooldown
    @level_group.command(name="gain")
    @commands.has_permissions(manage_guild=True)
    async def level_gain(self, ctx: commands.Context, min_xp: int, max_xp: int):
        min_xp = max(1, int(min_xp)); max_xp = max(min_xp, int(max_xp))
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, gain_min, gain_max) VALUES(?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET gain_min=excluded.gain_min, gain_max=excluded.gain_max",
            ctx.guild.id, min_xp, max_xp
        )
        await ctx.send(f"✅ Gain set to **{min_xp}–{max_xp}** XP.")

    @level_group.command(name="cooldown")
    @commands.has_permissions(manage_guild=True)
    async def level_cooldown(self, ctx: commands.Context, seconds: int):
        seconds = max(1, int(seconds))
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, cooldown_s) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET cooldown_s=excluded.cooldown_s",
            ctx.guild.id, seconds
        )
        await ctx.send(f"✅ Cooldown set to **{seconds}s**.")

    # Thresholds and min chars
    @level_group.command(name="thresholds")
    @commands.has_permissions(manage_guild=True)
    async def level_thresholds(self, ctx: commands.Context, short_cnt: int, short_sec: int, long_cnt: int, long_sec: int):
        short_cnt = max(3, int(short_cnt)); short_sec = max(2, int(short_sec))
        long_cnt = max(short_cnt + 1, int(long_cnt)); long_sec = max(short_sec + 1, int(long_sec))
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, short_window_s, short_threshold, long_window_s, long_threshold) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "short_window_s=excluded.short_window_s, short_threshold=excluded.short_threshold, "
            "long_window_s=excluded.long_window_s, long_threshold=excluded.long_threshold",
            ctx.guild.id, short_sec, short_cnt, long_sec, long_cnt
        )
        await ctx.send(f"✅ Thresholds set: **{short_cnt}/{short_sec}s**, **{long_cnt}/{long_sec}s**.")

    @level_group.command(name="minchars")
    @commands.has_permissions(manage_guild=True)
    async def level_minchars(self, ctx: commands.Context, count: int):
        count = max(0, int(count))
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, min_chars) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET min_chars=excluded.min_chars",
            ctx.guild.id, count
        )
        await ctx.send(f"✅ Minimum characters set to **{count}**.")

    # Role mode (highest vs stack)
    @level_group.command(name="rolemode")
    @commands.has_permissions(manage_guild=True)
    async def level_rolemode(self, ctx: commands.Context, mode: str):
        mode = mode.lower()
        if mode not in {"highest", "stack"}:
            return await ctx.send("use: `,level rolemode highest` or `,level rolemode stack`")
        val = 1 if mode == "highest" else 0
        await self.bot.db.execute(
            "INSERT INTO xp_settings(guild_id, roles_highest_only) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET roles_highest_only=excluded.roles_highest_only",
            ctx.guild.id, val
        )
        await ctx.send(f"✅ Role mode set to **{mode}**.")

    # Whitelist (bypass anti-abuse)
    @level_group.group(name="whitelist", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def level_whitelist(self, ctx: commands.Context):
        await ctx.send("use: `,level whitelist add @user` / `remove @user` / `list`")

    @level_whitelist.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def whitelist_add(self, ctx: commands.Context, member: discord.Member):
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO xp_whitelist(guild_id,user_id) VALUES(?,?)",
            ctx.guild.id, member.id
        )
        await ctx.send(f"✅ Whitelisted {member.mention} from anti-abuse.")

    @level_whitelist.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def whitelist_remove(self, ctx: commands.Context, member: discord.Member):
        await self.bot.db.execute(
            "DELETE FROM xp_whitelist WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        await ctx.send(f"✅ Removed {member.mention} from whitelist.")

    @level_whitelist.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def whitelist_list(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall("SELECT user_id FROM xp_whitelist WHERE guild_id=?", ctx.guild.id)
        if not rows:
            return await ctx.send("Whitelist is empty.")
        names = []
        for r in rows:
            m = ctx.guild.get_member(r["user_id"])
            names.append(m.mention if m else f"<@{r['user_id']}>")
        await ctx.send("**Whitelisted:** " + ", ".join(names))

    # Ignore channels/roles
    @level_group.group(name="ignore", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def level_ignore(self, ctx: commands.Context):
        await ctx.send("use: `,level ignore channel add/remove/list` or `,level ignore role add/remove/list`")

    @level_ignore.group(name="channel", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def ignore_channel_group(self, ctx: commands.Context):
        await ctx.send("use: `,level ignore channel add #chan` / `remove #chan` / `list`")

    @ignore_channel_group.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def ignore_channel_add(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO xp_ignore_channels(guild_id,channel_id) VALUES(?,?)",
            ctx.guild.id, channel.id
        )
        await ctx.send(f"✅ Ignoring {channel.mention} for leveling.")

    @ignore_channel_group.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def ignore_channel_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "DELETE FROM xp_ignore_channels WHERE guild_id=? AND channel_id=?",
            ctx.guild.id, channel.id
        )
        await ctx.send(f"✅ No longer ignoring {channel.mention}.")

    @ignore_channel_group.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def ignore_channel_list(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM xp_ignore_channels WHERE guild_id=?",
            ctx.guild.id
        )
        if not rows:
            return await ctx.send("No ignored channels.")
        chans = []
        for r in rows:
            ch = ctx.guild.get_channel(r["channel_id"])
            chans.append(ch.mention if ch else f"<#{r['channel_id']}>")
        await ctx.send("**Ignored channels:** " + ", ".join(chans))

    @level_ignore.group(name="role", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def ignore_role_group(self, ctx: commands.Context):
        await ctx.send("use: `,level ignore role add @role` / `remove @role` / `list`")

    @ignore_role_group.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def ignore_role_add(self, ctx: commands.Context, role: discord.Role):
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO xp_ignore_roles(guild_id,role_id) VALUES(?,?)",
            ctx.guild.id, role.id
        )
        await ctx.send(f"✅ Members with {role.mention} are ignored for leveling.")

    @ignore_role_group.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def ignore_role_remove(self, ctx: commands.Context, role: discord.Role):
        await self.bot.db.execute(
            "DELETE FROM xp_ignore_roles WHERE guild_id=? AND role_id=?",
            ctx.guild.id, role.id
        )
        await ctx.send(f"✅ No longer ignoring {role.mention}.")

    @ignore_role_group.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def ignore_role_list(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall(
            "SELECT role_id FROM xp_ignore_roles WHERE guild_id=?",
            ctx.guild.id
        )
        if not rows:
            return await ctx.send("No ignored roles.")
        roles = []
        for r in rows:
            role = ctx.guild.get_role(r["role_id"])
            roles.append(role.mention if role else f"<@&{r['role_id']}>")
        await ctx.send("**Ignored roles:** " + ", ".join(roles))

    # Level roles
    @level_group.group(name="role", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def level_role(self, ctx: commands.Context):
        await ctx.send("use: `,level role add <@role> <level>` / `remove <@role>` / `list`")

    @level_role.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def level_role_add(self, ctx: commands.Context, role: discord.Role, level: int):
        level = max(0, int(level))
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp_level_roles(guild_id, role_id, level) VALUES(?,?,?)",
            ctx.guild.id, role.id, level
        )
        await ctx.send("✅ role reward set.")

    @level_role.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def level_role_remove(self, ctx: commands.Context, role: discord.Role):
        await self.bot.db.execute(
            "DELETE FROM xp_level_roles WHERE guild_id=? AND role_id=?",
            ctx.guild.id, role.id
        )
        await ctx.send("✅ role reward removed.")

    @level_role.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def level_role_list(self, ctx: commands.Context):
        rows = await self.bot.db.fetchall(
            "SELECT role_id, level FROM xp_level_roles WHERE guild_id=? ORDER BY level ASC",
            ctx.guild.id
        )
        if not rows:
            return await ctx.send("no level roles configured.")
        lines = []
        for r in rows:
            role = ctx.guild.get_role(r["role_id"])
            if role:
                lines.append(f"{role.mention} — L{r['level']}")
        await ctx.send("\n".join(lines) if lines else "no level roles available.")

    # Manual suspend/unsuspend
    @level_group.command(name="suspend", usage="@user <duration>")
    @commands.has_permissions(manage_guild=True)
    async def level_suspend(self, ctx: commands.Context, member: discord.Member, duration: str):
        secs = duration_to_seconds(duration)
        if not secs or secs <= 0:
            return await ctx.send("duration examples: `10m`, `1h30m`, `2d`")
        until = utcnow() + timedelta(seconds=secs)
        row = await self.bot.db.fetchrow(
            "SELECT xp, level, last_award_at, strikes, opt_out FROM xp WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        xp = int(row["xp"]) if row else 0
        level = int(row["level"]) if row else 0
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ctx.guild.id, member.id, xp, level,
            row["last_award_at"] if row else None,
            iso(until),
            int(row["strikes"]) if row else 0,
            int(row["opt_out"]) if row else 0
        )
        await ctx.send(f"🚫 Suspended XP for {member.mention} until <t:{int(until.timestamp())}:R>.")

    @level_group.command(name="unsuspend", usage="@user")
    @commands.has_permissions(manage_guild=True)
    async def level_unsuspend(self, ctx: commands.Context, member: discord.Member):
        row = await self.bot.db.fetchrow(
            "SELECT xp, level, last_award_at, strikes, opt_out FROM xp WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        if not row:
            return await ctx.send("no record for that user.")
        await self.bot.db.execute(
            "UPDATE xp SET suspend_until=NULL WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        await ctx.send(f"✅ Unsuspended XP for {member.mention}.")

    # Opt-out/in
    @level_group.command(name="optout")
    async def level_optout(self, ctx: commands.Context):
        await self.bot.db.execute(
            "INSERT INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,1) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET opt_out=1",
            ctx.guild.id, ctx.author.id, 0, 0, None, None, 0
        )
        await ctx.send("👌 You will no longer receive XP. Use `,level optin` to re-enable.")

    @level_group.command(name="optin")
    async def level_optin(self, ctx: commands.Context):
        await self.bot.db.execute(
            "INSERT INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,0) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET opt_out=0",
            ctx.guild.id, ctx.author.id, 0, 0, None, None, 0
        )
        await ctx.send("👍 You will now receive XP again.")

    # Admin setters
    @commands.command(name="setxp", usage="<user> <xp>")
    @commands.has_permissions(manage_guild=True)
    async def setxp(self, ctx: commands.Context, member: discord.Member, xp: int):
        xp = max(0, int(xp))
        row = await self.bot.db.fetchrow(
            "SELECT level, last_award_at, suspend_until, strikes, opt_out FROM xp WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        level = int(row["level"]) if row else 0
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ctx.guild.id, member.id, xp, level,
            row["last_award_at"] if row else None,
            row["suspend_until"] if row else None,
            int(row["strikes"]) if row else 0,
            int(row["opt_out"]) if row else 0
        )
        await ctx.send("ok.")

    @commands.command(name="setlvl", aliases=["setlevel"], usage="<user> <level>")
    @commands.has_permissions(manage_guild=True)
    async def setlvl(self, ctx: commands.Context, member: discord.Member, level: int):
        level = max(0, int(level))
        row = await self.bot.db.fetchrow(
            "SELECT xp, last_award_at, suspend_until, strikes, opt_out FROM xp WHERE guild_id=? AND user_id=?",
            ctx.guild.id, member.id
        )
        xp = int(row["xp"]) if row else 0
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO xp(guild_id,user_id,xp,level,last_award_at,suspend_until,strikes,opt_out) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ctx.guild.id, member.id, xp, level,
            row["last_award_at"] if row else None,
            row["suspend_until"] if row else None,
            int(row["strikes"]) if row else 0,
            int(row["opt_out"]) if row else 0
        )
        await self._grant_level_roles(member, level)
        await ctx.send("ok.")

# =============== extension setup ===============
async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
