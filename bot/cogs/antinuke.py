import asyncio
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List, Deque, Tuple, Any

import discord
from discord.ext import commands

# ===============================
# ---------- Utilities ----------
# ===============================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def clamp_bool(x: Optional[bool]) -> Optional[bool]:
    return None if x is None else bool(x)

def safe_getattr(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default

def first(seq, pred):
    for x in seq:
        if pred(x):
            return x
    return None

# Discord version compatibility for channel classes (avoid NameError on older libs)
ForumChannel = getattr(discord, "ForumChannel", None)
StageChannel = getattr(discord, "StageChannel", None)
TEXT_CH_TYPES = tuple([discord.TextChannel] + ([ForumChannel] if ForumChannel else []))
VOICE_CH_TYPES = tuple([discord.VoiceChannel] + ([StageChannel] if StageChannel else []))
CAT_CH_TYPES = (discord.CategoryChannel,)

# -----------------------------
# Username sanitization helpers
# -----------------------------
import unicodedata
_CONFUSABLES = {
    "𝔞":"a","𝖆":"a","𝗮":"a","𝘢":"a","𝙖":"a","𝚊":"a","ａ":"a","Ⓐ":"A","🄰":"A",
    "𝔟":"b","𝖇":"b","𝗯":"b","𝘣":"b","𝙗":"b","𝚋":"b","ｂ":"b",
    "𝔠":"c","𝖈":"c","𝗰":"c","𝘤":"c","𝙘":"c","𝚌":"c","ｃ":"c",
    "𝔡":"d","𝖉":"d","𝗱":"d","𝘥":"d","𝙙":"d","𝚍":"d","ｄ":"d",
    "𝔢":"e","𝖊":"e","𝗲":"e","𝘦":"e","𝙚":"e","𝚎":"e","ｅ":"e",
    "𝔣":"f","𝖋":"f","𝗳":"f","𝘧":"f","𝙛":"f","𝚏":"f","ｆ":"f",
    "𝔤":"g","𝖌":"g","𝗴":"g","𝘨":"g","𝙜":"g","𝚐":"g","ｇ":"g",
    "𝔥":"h","𝖍":"h","𝗵":"h","𝘩":"h","𝙝":"h","𝚑":"h","ｈ":"h",
    "𝔦":"i","𝖎":"i","𝗶":"i","𝘪":"i","𝙞":"i","𝚒":"i","ｉ":"i","Ⅰ":"I",
    "𝔧":"j","𝖏":"j","𝗷":"j","𝘫":"j","𝙟":"j","𝚓":"j","ｊ":"j",
    "𝔨":"k","𝖐":"k","𝗸":"k","𝘬":"k","𝙠":"k","𝚔":"k","ｋ":"k",
    "𝔩":"l","𝖑":"l","𝗹":"l","𝘭":"l","𝙡":"l","𝚕":"l","ｌ":"l",
    "𝔪":"m","𝖒":"m","𝗺":"m","𝘮":"m","𝙢":"m","𝚖":"m","ｍ":"m",
    "𝔫":"n","𝖓":"n","𝗻":"n","𝘯":"n","𝙣":"n","𝚗":"n","ｎ":"n",
    "𝔬":"o","𝖔":"o","𝗼":"o","𝘰":"o","𝙤":"o","𝚘":"o","ｏ":"o","Ⓞ":"O","🅞":"O","🅾":"O",
    "𝔭":"p","𝖕":"p","𝗽":"p","𝘱":"p","𝙥":"p","𝚙":"p","ｐ":"p",
    "𝔮":"q","𝖖":"q","𝗾":"q","𝘲":"q","𝙦":"q","𝚚":"q","ｑ":"q",
    "𝔯":"r","𝖗":"r","𝗿":"r","𝘳":"r","𝙧":"r","𝚛":"r","ｒ":"r",
    "𝔰":"s","𝖘":"s","𝘴":"s","𝙨":"s","𝚜":"s","ｓ":"s","Ⓢ":"S","🅢":"S",
    "𝔱":"t","𝖙":"t","𝘵":"t","𝙩":"t","𝚝":"t","ｔ":"t",
    "𝔲":"u","𝖚":"u","𝘶":"u","𝙪":"u","𝚞":"u","ｕ":"u",
    "𝔳":"v","𝖛":"v","𝘷":"v","𝙫":"v","𝚟":"v","ｖ":"v",
    "𝔴":"w","𝖜":"w","𝘸":"w","𝙬":"w","𝚠":"w","ｗ":"w",
    "𝔵":"x","𝖝":"x","𝘹":"x","𝙭":"x","𝚡":"x","ｘ":"x",
    "𝔶":"y","𝖞":"y","𝘺":"y","𝙮":"y","𝚢":"y","ｙ":"y",
    "𝔷":"z","𝖟":"z","𝘻":"z","𝙯":"z","𝚣":"z","ｚ":"z",
}
_HOIST_PREFIXES = tuple("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~￼")

def sanitize_text(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = "".join(_CONFUSABLES.get(ch, ch) for ch in name)
    name = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    name = "".join(ch for ch in name if ch.isprintable())
    while name and name[0] in _HOIST_PREFIXES:
        name = name[1:]
    name = name.strip() or "Member"
    return name[:32]

# ===============================
# ------- Persistence layer -----
# ===============================

@dataclass
class Threshold:
    count: int
    interval: int  # seconds

DEFAULT_THRESHOLDS = {
    "channel_delete": Threshold(3, 10),
    "channel_create": Threshold(5, 10),
    "role_delete":    Threshold(3, 10),
    "ban":            Threshold(3, 10),
    "kick":           Threshold(3, 10),
    "webhook_create": Threshold(5, 10),
}

class JSONKV:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

class AntiNukeConfigStore(JSONKV):
    """
    {
      guild_id: {
        "enabled": bool,
        "log_channel_id": int|null,
        "vanity_protect": bool,
        "vanity_code": str|null,
        "punishment": "jail"|"strip"|"ban",
        "thresholds": { name: {"count": int, "interval": int} },
        "whitelist": [int],
        "admins": [int],               # wladmins
        "sanitize_on_join": bool
      }
    }
    """
    def __init__(self, path="antinuke_config.json"):
        super().__init__(path)

    # ----- SQL HOOKS (replace with your DB) -----
    def load_guild_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        return self.data.get(str(guild_id))

    def save_guild_config(self, guild_id: int, cfg: Dict[str, Any]):
        self.data[str(guild_id)] = cfg
        self.save()

    def g(self, guild_id: int) -> Dict[str, Any]:
        g = self.load_guild_config(guild_id) or {}
        g.setdefault("enabled", True)
        g.setdefault("log_channel_id", None)
        g.setdefault("vanity_protect", False)
        g.setdefault("vanity_code", None)
        g.setdefault("punishment", "jail")
        g.setdefault("thresholds", {k: asdict(v) for k, v in DEFAULT_THRESHOLDS.items()})
        g.setdefault("whitelist", [])
        g.setdefault("admins", [])
        g.setdefault("sanitize_on_join", False)
        self.save_guild_config(guild_id, g)
        return g

class AntiNukeStateStore(JSONKV):
    """
    {
      guild_id: {
        "lock": {
          "active": bool,
          "channels": { channel_id: {view, send, connect} }
        }
      }
    }
    """
    def __init__(self, path="antinuke_state.json"):
        super().__init__(path)

    def g(self, guild_id: int) -> Dict[str, Any]:
        return self.data.setdefault(str(guild_id), {})

# ===============================
# ----- Action rate tracking ----
# ===============================

class ActionTracker:
    def __init__(self):
        self._data: Dict[int, Dict[int, Dict[str, Deque[datetime]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(deque))
        )

    def record(self, guild_id: int, user_id: int, action: str, keep_seconds: int):
        d = self._data[guild_id][user_id][action]
        now = utcnow()
        d.append(now)
        cutoff = now - timedelta(seconds=keep_seconds)
        while d and d[0] < cutoff:
            d.popleft()
        return len(d)

    def count(self, guild_id: int, user_id: int, action: str, within: int) -> int:
        d = self._data[guild_id][user_id][action]
        cutoff = utcnow() - timedelta(seconds=within)
        while d and d[0] < cutoff:
            d.popleft()
        return len(d)

# ===============================
# --------- The Cog -------------
# ===============================

class AntiNuke(commands.Cog):
    """
    Hardened anti-nuke with vanity guard (instant revert + burst retries + sentinel),
    sanitize (auto/manual), lock/unlock, jail, logging, whitelist & wladmins.

    *All commands are restricted to server owner or wladmins only.*
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = AntiNukeConfigStore()
        self.state = AntiNukeStateStore()
        self.tracker = ActionTracker()

        # Vanity watchers per guild
        self._vanity_sentinels: Dict[int, asyncio.Task] = {}
        self._vanity_retry_tasks: Dict[int, asyncio.Task] = {}

    # ---------- Global command gate (OWNER + WLADMINS ONLY) ----------
    async def cog_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False
        if ctx.author.id == ctx.guild.owner_id:
            return True
        cfg = self.config.g(ctx.guild.id)
        if ctx.author.id in cfg.get("admins", []):  # wladmins
            return True
        await ctx.reply("Only the **server owner** or **wladmins** can use this bot.")
        return False

    # ---------- Lifecycle: start vanity sentinels ----------
    @commands.Cog.listener()
    async def on_ready(self):
        # Start a watcher for every guild that enabled vanity_protect
        for g in self.bot.guilds:
            await self._ensure_vanity_sentinel(g)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._ensure_vanity_sentinel(guild)

    async def _ensure_vanity_sentinel(self, guild: discord.Guild):
        cfg = self.config.g(guild.id)
        if not cfg.get("vanity_protect") or not cfg.get("vanity_code"):
            # If disabled but a task exists, cancel it
            t = self._vanity_sentinels.pop(guild.id, None)
            if t:
                t.cancel()
            return
        if guild.id in self._vanity_sentinels and not self._vanity_sentinels[guild.id].done():
            return
        self._vanity_sentinels[guild.id] = asyncio.create_task(self._vanity_sentinel_loop(guild))

    # ---------- Logging ----------
    async def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        cfg = self.config.g(guild.id)
        chan_id = cfg.get("log_channel_id")
        chan = guild.get_channel(chan_id) if chan_id else None
        if chan is None:
            existing = first(
                guild.text_channels, lambda c: c.name.lower() in ("anti-nuke-logs", "antinuke-logs")
            )
            if existing:
                cfg["log_channel_id"] = existing.id
                self.config.save_guild_config(guild.id, cfg)
                return existing
            try:
                chan = await guild.create_text_channel("anti-nuke-logs")
                cfg["log_channel_id"] = chan.id
                self.config.save_guild_config(guild.id, cfg)
            except Exception:
                chan = None
        return chan

    async def log(self, guild: discord.Guild, title: str, description: str = "", *, color: int = 0xFF5555):
        ch = await self.get_log_channel(guild)
        if not ch:
            return
        emb = discord.Embed(title=title, description=description or discord.Embed.Empty, color=color, timestamp=utcnow())
        try:
            await ch.send(embed=emb)
        except Exception:
            pass

    # ---------- Config helpers ----------
    def get_threshold(self, guild_id: int, key: str) -> Threshold:
        g = self.config.g(guild_id)
        t = g["thresholds"].get(key, asdict(DEFAULT_THRESHOLDS[key]))
        return Threshold(**t)

    def is_whitelisted(self, guild_id: int, user_id: int) -> bool:
        cfg = self.config.g(guild_id)
        return user_id in cfg.get("whitelist", []) or user_id in cfg.get("admins", [])

    # ---------- Punish ----------
    async def punish(self, guild: discord.Guild, attacker: discord.Member, reason: str):
        if self.is_whitelisted(guild.id, attacker.id):
            await self.log(guild, "Anti-Nuke: Whitelisted action", f"{attacker} | {reason}", color=0x3498DB)
            return

        if attacker == guild.owner:
            await self.log(guild, "Anti-Nuke: Owner action detected", f"{attacker} | {reason}", color=0xF1C40F)
            return

        me = guild.me
        if me.top_role <= attacker.top_role:
            await self.log(guild, "Anti-Nuke: Insufficient hierarchy", f"Cannot punish `{attacker}`. {reason}", color=0xF1C40F)
            return

        mode = self.config.g(guild.id).get("punishment", "jail")

        if mode == "ban":
            try:
                await guild.ban(attacker, reason=f"Anti-Nuke: {reason}", delete_message_days=0)
                await self.log(guild, "Anti-Nuke: Banned attacker", f"{attacker} | {reason}", color=0xE74C3C)
                return
            except Exception:
                pass

        # Strip roles
        try:
            to_remove = [r for r in attacker.roles if r != guild.default_role and r < me.top_role]
            if to_remove:
                await attacker.remove_roles(*to_remove, reason=f"Anti-Nuke strip: {reason}")
        except Exception:
            pass

        # Jail
        jailed = discord.utils.get(guild.roles, name="Jailed")
        if not jailed:
            try:
                jailed = await guild.create_role(name="Jailed", permissions=discord.Permissions.none(), reason="Anti-Nuke: create jailed role")
            except Exception:
                jailed = None

        if jailed:
            try:
                await attacker.add_roles(jailed, reason=f"Anti-Nuke: {reason}")
            except Exception:
                pass
            for ch in list(guild.channels):
                try:
                    if isinstance(ch, TEXT_CH_TYPES + VOICE_CH_TYPES):
                        await ch.set_permissions(jailed, view_channel=False, send_messages=False, add_reactions=False, connect=False, speak=False, reason="Anti-Nuke: jail lockdown")
                except Exception:
                    continue

        await self.log(guild, "Anti-Nuke: Attacker jailed", f"{attacker} | {reason}", color=0xE74C3C)

    # ---------- Vanity helpers ----------
    async def _fetch_current_vanity(self, guild: discord.Guild) -> Optional[str]:
        # Prefer property if present (updated by gateway)
        prop = safe_getattr(guild, "vanity_url_code", None)
        if prop:
            return prop
        # Fallback HTTP GET
        try:
            inv = await guild.vanity_invite()
            return inv.code if inv else None
        except Exception:
            return None

    async def set_vanity_code(self, guild: discord.Guild, code: str) -> bool:
        # Try the typed interface first
        try:
            await guild.edit(vanity_code=code, reason="Anti-Nuke: Vanity revert")
            return True
        except TypeError:
            pass
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            pass
        # Raw route fallback
        try:
            from discord.http import Route
            payload = {"vanity_url_code": code}
            await self.bot.http.request(Route("PATCH", "/guilds/{guild_id}", guild_id=guild.id), json=payload)
            return True
        except Exception:
            return False

    async def _burst_reclaim(self, guild: discord.Guild, want: str, seconds: float = 8.0) -> bool:
        """
        Very fast retries for a short window to beat vanity snipes or transient 5xx/429.
        Starts at 50ms and backs off to 250ms within the window to be rate-limit friendly.
        """
        deadline = utcnow() + timedelta(seconds=seconds)
        delay = 0.05
        ok = False
        while utcnow() < deadline:
            ok = await self.set_vanity_code(guild, want)
            if ok:
                return True
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.25)
        return False

    async def _vanity_sentinel_loop(self, guild: discord.Guild):
        """
        Adaptive watcher: low-cost periodic checks to catch missed events.
        - Normal interval: 2.0s
        - After any detected change or failure: elevate to 0.25s for ~20s
        Keeps well under common rate limits.
        """
        elevated_until: Optional[datetime] = None
        while True:
            try:
                cfg = self.config.g(guild.id)
                if not cfg.get("vanity_protect") or not cfg.get("vanity_code"):
                    return  # stop quietly
                want = cfg["vanity_code"]

                current = await self._fetch_current_vanity(guild)
                if current and current != want:
                    # Detected drift outside events → reclaim
                    ok = await self.set_vanity_code(guild, want)
                    if not ok:
                        await self._burst_reclaim(guild, want, seconds=8.0)
                    elevated_until = utcnow() + timedelta(seconds=20)
                    await self.log(guild, "Vanity Sentinel: Reclaimed", f"Drift `{current}` → `{want}`.", color=0x3498DB)

                # Interval control
                now = utcnow()
                fast = elevated_until and now < elevated_until
                await asyncio.sleep(0.25 if fast else 2.0)
            except asyncio.CancelledError:
                return
            except Exception:
                # Never crash; short nap before retry loop continues
                await asyncio.sleep(1.0)

    # ------------- Commands -------------

    @commands.group(name="antinuke", invoke_without_command=True)
    @commands.guild_only()
    async def antinuke_group(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        enabled = cfg["enabled"]
        vanity = cfg["vanity_protect"]
        vcode = cfg["vanity_code"]
        tlines = [f"• {k}: {v['count']} in {v['interval']}s" for k, v in cfg["thresholds"].items()]
        await ctx.reply(
            f"**AntiNuke status**\n"
            f"Enabled: `{enabled}`\n"
            f"Logging channel: {ctx.guild.get_channel(cfg['log_channel_id']).mention if cfg['log_channel_id'] else 'auto'}\n"
            f"Vanity protect: `{vanity}` {'('+vcode+')' if vanity and vcode else ''}\n"
            f"Punishment: `{cfg['punishment']}`\n"
            f"Sanitize on join: `{cfg['sanitize_on_join']}`\n"
            f"Thresholds:\n" + "\n".join(tlines)
        )

    @antinuke_group.command(name="enable")
    @commands.guild_only()
    async def antinuke_enable(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        cfg["enabled"] = True
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply("✅ AntiNuke enabled.")

    @antinuke_group.command(name="disable")
    @commands.guild_only()
    async def antinuke_disable(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        cfg["enabled"] = False
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply("✅ AntiNuke disabled.")

    @antinuke_group.command(name="setlog")
    @commands.guild_only()
    async def antinuke_setlog(self, ctx: commands.Context, channel: discord.TextChannel):
        cfg = self.config.g(ctx.guild.id)
        cfg["log_channel_id"] = channel.id
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Logging channel set to {channel.mention}")

    @antinuke_group.command(name="setpunish")
    @commands.guild_only()
    async def antinuke_setpunish(self, ctx: commands.Context, mode: str):
        mode = mode.lower()
        if mode not in {"jail", "strip", "ban"}:
            return await ctx.reply("Use one of: `jail`, `strip`, `ban`.")
        cfg = self.config.g(ctx.guild.id)
        cfg["punishment"] = mode
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Punishment mode set to `{mode}`.")

    @commands.command(name="setvanity")
    @commands.guild_only()
    async def set_vanity(self, ctx: commands.Context, code: str):
        code = code.strip().lower()
        if not (3 <= len(code) <= 32) or not code.replace("-", "").isalnum():
            return await ctx.reply("Vanity must be 3–32 chars (letters/digits, dashes allowed).")
        cfg = self.config.g(ctx.guild.id)
        cfg["vanity_protect"] = True
        cfg["vanity_code"] = code
        self.config.save_guild_config(ctx.guild.id, cfg)
        await self._ensure_vanity_sentinel(ctx.guild)
        ok = await self.set_vanity_code(ctx.guild, code)
        if ok:
            await self.log(ctx.guild, "Vanity Guard: Baseline set", f"Set to `{code}` by {ctx.author}", color=0x2ECC71)
            await ctx.reply(f"✅ Vanity set to `discord.gg/{code}` and protection enabled.")
        else:
            await ctx.reply("⚠️ API refused to set vanity. Ensure the server has `VANITY_URL` and the bot has **Manage Server**.")

    # --------- Whitelist ----------
    @commands.group(name="wl", invoke_without_command=True)
    @commands.guild_only()
    async def wl_group(self, ctx: commands.Context):
        await ctx.reply("Usage: `,wl add @user` | `,wl remove @user` | `,wl list`")

    @wl_group.command(name="add")
    @commands.guild_only()
    async def wl_add(self, ctx: commands.Context, member: discord.Member):
        cfg = self.config.g(ctx.guild.id)
        wl = set(cfg.get("whitelist", []))
        wl.add(member.id)
        cfg["whitelist"] = list(wl)
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Whitelisted {member.mention} from anti-nuke punishments.")

    @wl_group.command(name="remove")
    @commands.guild_only()
    async def wl_remove(self, ctx: commands.Context, member: discord.Member):
        cfg = self.config.g(ctx.guild.id)
        wl = set(cfg.get("whitelist", []))
        wl.discard(member.id)
        cfg["whitelist"] = list(wl)
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Removed {member.mention} from whitelist.")

    @wl_group.command(name="list")
    @commands.guild_only()
    async def wl_list(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        wl = cfg.get("whitelist", [])
        if not wl:
            return await ctx.reply("Whitelist is empty.")
        names = []
        for uid in wl:
            m = ctx.guild.get_member(uid)
            names.append(m.mention if m else f"`{uid}`")
        await ctx.reply("**Whitelisted:** " + ", ".join(names))

    # --------- WL Admins ----------
    @commands.group(name="wladmin", invoke_without_command=True)
    @commands.guild_only()
    async def wladmin_group(self, ctx: commands.Context):
        await ctx.reply("Usage: `,wladmin add @user` | `,wladmin remove @user` | `,wladmin list`\n(Only **server owner** can modify wladmins.)")

    def _owner_only(self, ctx: commands.Context) -> bool:
        return ctx.author.id == ctx.guild.owner_id

    @wladmin_group.command(name="add")
    @commands.guild_only()
    async def wladmin_add(self, ctx: commands.Context, member: discord.Member):
        if not self._owner_only(ctx):
            return await ctx.reply("Only the **server owner** can modify wladmins.")
        cfg = self.config.g(ctx.guild.id)
        admins = set(cfg.get("admins", []))
        admins.add(member.id)
        cfg["admins"] = list(admins)
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Added {member.mention} as wladmin.")

    @wladmin_group.command(name="remove")
    @commands.guild_only()
    async def wladmin_remove(self, ctx: commands.Context, member: discord.Member):
        if not self._owner_only(ctx):
            return await ctx.reply("Only the **server owner** can modify wladmins.")
        cfg = self.config.g(ctx.guild.id)
        admins = set(cfg.get("admins", []))
        admins.discard(member.id)
        cfg["admins"] = list(admins)
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply(f"✅ Removed {member.mention} from wladmins.")

    @wladmin_group.command(name="list")
    @commands.guild_only()
    async def wladmin_list(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        ad = cfg.get("admins", [])
        if not ad:
            return await ctx.reply("No wladmins set.")
        names = []
        for uid in ad:
            m = ctx.guild.get_member(uid)
            names.append(m.mention if m else f"`{uid}`")
        await ctx.reply("**WL Admins:** " + ", ".join(names))

    # --------- Sanitize (manual + toggle) ----------
    @commands.group(name="sanitize", invoke_without_command=True)
    @commands.guild_only()
    async def sanitize_group(self, ctx: commands.Context, *members: discord.Member):
        if members:
            await self._sanitize_members(ctx, list(members))
        else:
            await ctx.reply("Usage: `,sanitize @user [@user2 ...]` | `,sanitize on` | `,sanitize off`.")

    @sanitize_group.command(name="on")
    @commands.guild_only()
    async def sanitize_on(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        cfg["sanitize_on_join"] = True
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply("✅ Sanitize-on-join enabled.")

    @sanitize_group.command(name="off")
    @commands.guild_only()
    async def sanitize_off(self, ctx: commands.Context):
        cfg = self.config.g(ctx.guild.id)
        cfg["sanitize_on_join"] = False
        self.config.save_guild_config(ctx.guild.id, cfg)
        await ctx.reply("✅ Sanitize-on-join disabled.")

    async def _sanitize_members(self, ctx: commands.Context, members: List[discord.Member]):
        changed, failed = [], []
        for m in members:
            try:
                new = sanitize_text(m.display_name)
                if new != m.display_name:
                    await m.edit(nick=new, reason=f"Sanitize by {ctx.author}")
                changed.append((m, new))
            except discord.Forbidden:
                failed.append((m, "forbidden (hierarchy)"))
            except discord.HTTPException:
                failed.append((m, "HTTP error"))
        msg = []
        if changed:
            msg.append("✅ **Sanitized:** " + ", ".join(f"`{m.display_name}`→`{new}`" for m, new in changed))
        if failed:
            msg.append("⚠️ **Failed:** " + ", ".join(f"{m} ({why})" for m, why in failed))
        await ctx.reply("\n".join(msg) if msg else "Nothing to change.")

    # --------- Lock / Unlock ----------
    @commands.command(name="lockdown")
    @commands.guild_only()
    async def lock(self, ctx: commands.Context, *args):
        """
        ,lock
        ,lock bypass #channel #another
        - Denies @everyone on all channels (except bypassed)
        - Deletes all webhooks
        """
        bypass_ids: set[int] = set()
        if args and args[0].lower() == "bypass":
            for ch in ctx.message.channel_mentions:
                bypass_ids.add(ch.id)

        guild = ctx.guild
        state_g = self.state.g(guild.id)
        state_g["lock"] = state_g.get("lock", {"active": False, "channels": {}})
        lock_state = state_g["lock"]
        if lock_state.get("active"):
            return await ctx.reply("Server is already locked.")

        await ctx.reply("🔒 Locking server…")

        everyone = guild.default_role
        changed = {}
        for ch in list(guild.channels):
            if ch.id in bypass_ids:
                continue
            try:
                if isinstance(ch, TEXT_CH_TYPES + VOICE_CH_TYPES + CAT_CH_TYPES):
                    ow = ch.overwrites_for(everyone)
                    prev = {
                        "view": ow.view_channel,
                        "send": safe_getattr(ow, "send_messages", None),
                        "connect": safe_getattr(ow, "connect", None),
                    }
                    changed[str(ch.id)] = prev

                    if isinstance(ch, TEXT_CH_TYPES):
                        await ch.set_permissions(everyone, view_channel=False, send_messages=False, add_reactions=False, reason=f"Lockdown by {ctx.author}")
                    elif isinstance(ch, VOICE_CH_TYPES):
                        await ch.set_permissions(everyone, view_channel=False, connect=False, speak=False, stream=False, reason=f"Lockdown by {ctx.author}")
                    elif isinstance(ch, CAT_CH_TYPES):
                        await ch.set_permissions(everyone, view_channel=False, send_messages=False, connect=False, reason=f"Lockdown by {ctx.author}")
            except Exception:
                continue

        lock_state["channels"] = changed
        lock_state["active"] = True
        self.state.save()

        # Delete all webhooks
        deleted = 0
        for ch in guild.text_channels:
            try:
                for wh in await ch.webhooks():
                    try:
                        await wh.delete(reason=f"Lockdown by {ctx.author}")
                        deleted += 1
                    except Exception:
                        continue
            except Exception:
                continue

        await self.log(guild, "Server Locked", f"By {ctx.author}. Webhooks deleted: {deleted}", color=0x9B59B6)
        await ctx.reply(f"✅ Locked. Deleted **{deleted}** webhooks. Use `,unlock` to restore.")

    @commands.command(name="unlockdown")
    @commands.guild_only()
    async def unlock(self, ctx: commands.Context):
        guild = ctx.guild
        state_g = self.state.g(guild.id)
        lock_state = state_g.get("lock", {"active": False, "channels": {}})

        if not lock_state.get("active"):
            return await ctx.reply("Server isn’t locked.")

        everyone = guild.default_role
        restored = 0
        for cid, prev in lock_state.get("channels", {}).items():
            ch = guild.get_channel(int(cid))
            if not ch:
                continue
            try:
                ow = ch.overwrites_for(everyone)
                ow.view_channel = clamp_bool(prev.get("view"))
                if isinstance(ch, TEXT_CH_TYPES + CAT_CH_TYPES):
                    ow.send_messages = clamp_bool(prev.get("send"))
                if isinstance(ch, VOICE_CH_TYPES + CAT_CH_TYPES):
                    ow.connect = clamp_bool(prev.get("connect"))
                await ch.set_permissions(everyone, overwrite=ow, reason=f"Unlock by {ctx.author}")
                restored += 1
            except Exception:
                continue

        lock_state["active"] = False
        lock_state["channels"] = {}
        self.state.save()

        await self.log(guild, "Server Unlocked", f"By {ctx.author}. Restored {restored} channels.", color=0x2ECC71)
        await ctx.reply(f"✅ Unlocked. Restored **{restored}** channels.")

    # --------- Jail ----------
    @commands.command(name="jail")
    @commands.guild_only()
    async def jail(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Jailed"):
        guild = ctx.guild
        me = guild.me
        if member == guild.owner or me.top_role <= member.top_role:
            return await ctx.reply("I can’t jail that member (owner or higher role).")

        jailed = discord.utils.get(guild.roles, name="Jailed")
        if not jailed:
            try:
                jailed = await guild.create_role(name="Jailed", permissions=discord.Permissions.none(), reason="Create jailed role")
            except Exception:
                return await ctx.reply("Couldn’t create Jailed role; missing permissions.")

        try:
            rm = [r for r in member.roles if r != guild.default_role and r < me.top_role]
            if rm:
                await member.remove_roles(*rm, reason=f"Jailed by {ctx.author}: {reason}")
        except Exception:
            pass

        try:
            await member.add_roles(jailed, reason=f"Jailed by {ctx.author}: {reason}")
        except Exception:
            return await ctx.reply("Couldn’t assign Jailed role (check my role position).")

        jail_channel = discord.utils.get(guild.text_channels, name="jail")
        if not jail_channel:
            try:
                jail_channel = await guild.create_text_channel("jail", reason="Create jail channel")
            except Exception:
                jail_channel = None

        for ch in list(guild.channels):
            try:
                if isinstance(ch, discord.TextChannel):
                    allow_here = (jail_channel and ch.id == jail_channel.id)
                    await ch.set_permissions(jailed, view_channel=allow_here, send_messages=allow_here, add_reactions=False, reason="Jailed role restrictions")
                elif isinstance(ch, VOICE_CH_TYPES):
                    await ch.set_permissions(jailed, view_channel=False, connect=False, speak=False, reason="Jailed role restrictions")
            except Exception:
                continue

        await self.log(guild, "Member Jailed", f"{member} by {ctx.author}\nReason: {reason}", color=0xE74C3C)
        await ctx.reply(f"✅ Jailed {member.mention}. {'See ' + jail_channel.mention if jail_channel else ''}")

    # ------------- Anti-nuke listeners -------------

    async def _attributed_actor(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int) -> Optional[discord.Member]:
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                if safe_getattr(entry.target, "id", None) == target_id and (utcnow() - entry.created_at).total_seconds() < 30:
                    if isinstance(entry.user, discord.Member):
                        return entry.user
                    return guild.get_member(entry.user.id) or await guild.fetch_member(entry.user.id)
        except Exception:
            return None
        return None

    async def _maybe_flag(self, guild: discord.Guild, actor: Optional[discord.Member], key: str, human: str):
        if actor is None:
            await self.log(guild, f"Anti-Nuke observed: {human}", "Actor unknown (audit log latency).")
            return
        if self.is_whitelisted(guild.id, actor.id):
            await self.log(guild, f"Anti-Nuke observed (whitelisted): {human}", f"Actor: {actor.mention}", color=0x2ECC71)
            return
        t = self.get_threshold(guild.id, key)
        n = self.tracker.record(guild.id, actor.id, key, t.interval)
        await self.log(guild, f"Anti-Nuke observed: {human}", f"Actor: {actor.mention} • Count: {n}/{t.count} in {t.interval}s", color=0xF39C12)
        if n >= t.count:
            await self.punish(guild, actor, f"{key} threshold exceeded")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not self.config.g(guild.id).get("enabled"):
            return
        actor = await self._attributed_actor(guild, discord.AuditLogAction.channel_delete, channel.id)
        await self._maybe_flag(guild, actor, "channel_delete", f"Channel deleted: {channel.name}")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not self.config.g(guild.id).get("enabled"):
            return
        actor = await self._attributed_actor(guild, discord.AuditLogAction.channel_create, channel.id)
        await self._maybe_flag(guild, actor, "channel_create", f"Channel created: {channel.name}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        if not self.config.g(guild.id).get("enabled"):
            return
        actor = await self._attributed_actor(guild, discord.AuditLogAction.role_delete, role.id)
        await self._maybe_flag(guild, actor, "role_delete", f"Role deleted: {role.name}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        if not self.config.g(guild.id).get("enabled"):
            return
        actor = await self._attributed_actor(guild, discord.AuditLogAction.ban, user.id)
        await self._maybe_flag(guild, actor, "ban", f"Ban: {user}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if not self.config.g(guild.id).get("enabled"):
            return
        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.kick):
                if entry.target.id == member.id and (utcnow() - entry.created_at).total_seconds() < 30:
                    actor = entry.user if isinstance(entry.user, discord.Member) else await guild.fetch_member(entry.user.id)
                    await self._maybe_flag(guild, actor, "kick", f"Kick: {member}")
                    return
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not self.config.g(guild.id).get("enabled"):
            return
        actor = await self._attributed_actor(guild, discord.AuditLogAction.webhook_create, channel.id)
        if actor:
            await self._maybe_flag(guild, actor, "webhook_create", f"Webhook created in {channel.name}")

    # Vanity guard: revert + punish + burst reclaim on change
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        cfg = self.config.g(after.id)
        if not cfg.get("vanity_protect"):
            return
        want = cfg.get("vanity_code")
        if not want:
            return
        try:
            current = safe_getattr(after, "vanity_url_code", None) or await self._fetch_current_vanity(after)
            if not current:
                return
            if current != want:
                # Attribute the changer
                actor = None
                try:
                    async for entry in after.audit_logs(limit=6, action=discord.AuditLogAction.guild_update):
                        if safe_getattr(entry.target, "id", None) != after.id:
                            continue
                        # Detect a vanity field change in the entry
                        changed_vanity = False
                        for obj in (getattr(entry, "before", None), getattr(entry, "after", None)):
                            if obj is None:
                                continue
                            if hasattr(obj, "vanity_url_code") or hasattr(obj, "vanity_url"):
                                changed_vanity = True
                                break
                        if changed_vanity and (utcnow() - entry.created_at).total_seconds() < 30:
                            actor = (entry.user if isinstance(entry.user, discord.Member)
                                     else after.get_member(entry.user.id) or await after.fetch_member(entry.user.id))
                            break
                except Exception:
                    actor = None

                # Revert now
                ok = await self.set_vanity_code(after, want)
                if not ok:
                    ok = await self._burst_reclaim(after, want, seconds=8.0)

                await self.log(after, "Vanity Guard: Reverted", f"Changed from `{current}` → `{want}`. Result: {'OK' if ok else 'FAILED'}", color=0x3498DB if ok else 0xE74C3C)

                if actor is not None:
                    if not self.is_whitelisted(after.id, actor.id) and actor != after.owner:
                        await self.punish(after, actor, "vanity URL changed")
                    else:
                        await self.log(after, "Vanity Guard: Change by whitelisted/owner", f"Actor: {actor}", color=0x2ECC71)

                # elevate sentinel polling briefly
                if after.id in self._vanity_sentinels and not self._vanity_sentinels[after.id].done():
                    # sentinel loop itself elevates after drift; nothing extra needed
                    pass
        except Exception:
            pass

    # Sanitize on join
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = self.config.g(member.guild.id)
        if not cfg.get("sanitize_on_join", False):
            return
        try:
            new = sanitize_text(member.display_name)
            if new != member.display_name:
                await member.edit(nick=new, reason="Auto-sanitize on join")
        except Exception:
            pass

# ------------- Setup -------------

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
