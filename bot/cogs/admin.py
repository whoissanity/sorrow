from __future__ import annotations
import asyncio
import importlib
import importlib.util
import pkgutil
from pathlib import Path
from typing import Dict, List, Set, Tuple

import discord
from discord.ext import commands
from bot.utils.checks import is_guild_owner_or_admin
import xml.etree.ElementTree as ET

# =========================
# Reload mixin (inline)
# =========================

# If your cogs live somewhere else, change these two:
RELOAD_COG_PACKAGE = "bot.cogs"                     # dotted package path
RELOAD_COG_DIR = Path(__file__).resolve().parent    # filesystem path to cogs/

def _owner_or_server_owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        try:
            if await ctx.bot.is_owner(ctx.author):
                return True
        except Exception:
            pass
        if ctx.guild and ctx.author.id == ctx.guild.owner_id:
            return True
        await ctx.reply("Only the **bot owner** or this **server’s owner** can use this.")
        return False
    return commands.check(predicate)

def _discover_extensions(package: str = RELOAD_COG_PACKAGE, base_dir: Path = RELOAD_COG_DIR) -> List[str]:
    """Find dotted module names under your cogs package (recursively)."""
    exts: List[str] = []
    for modinfo in pkgutil.walk_packages([str(base_dir)], prefix=f"{package}."):
        exts.append(modinfo.name)
    for p in base_dir.rglob("*.py"):
        if p.name == "__init__.py":
            continue
        rel = p.relative_to(base_dir.parent)  # e.g. bot/cogs/music.py
        dotted = ".".join(rel.with_suffix("").parts)
        if dotted.startswith(package + "."):
            exts.append(dotted)
    return sorted(set(exts))

def _extension_origin(ext: str) -> Path | None:
    """Return the .py path for a dotted extension if resolvable."""
    try:
        spec = importlib.util.find_spec(ext)
    except Exception:
        return None
    if spec and spec.origin and spec.origin != "namespace":
        return Path(spec.origin)
    return None

class ReloadCommandsMixin:
    """
    Adds:
      ,reload all|changed|prune|<pattern ...>
      ,extensions
    into any cog that inherits this mixin.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ext_mtimes: Dict[str, float] = {}

    # ---- helpers ----
    def _match_targets(self, tokens: List[str], discovered: List[str]) -> List[str]:
        """Match tokens to extensions; supports '*', prefix*, *suffix, *middle*."""
        if not tokens:
            return []
        wanted: Set[str] = set()
        lowers = [t.lower() for t in tokens]
        for ext in discovered:
            name = ext.lower()
            for tok in lowers:
                if "*" in tok:
                    if tok == "*":
                        wanted.add(ext); continue
                    if tok.startswith("*") and tok.endswith("*"):
                        needle = tok.strip("*")
                        if needle and needle in name:
                            wanted.add(ext)
                    elif tok.endswith("*"):
                        if name.startswith(tok[:-1]):
                            wanted.add(ext)
                    elif tok.startswith("*"):
                        if name.endswith(tok[1:]):
                            wanted.add(ext)
                else:
                    if name == tok or name.endswith("." + tok) or tok in name:
                        wanted.add(ext)
        return sorted(wanted)

    def _current_loaded(self) -> Set[str]:
        return set(self.bot.extensions.keys())

    def _get_mtime(self, ext: str) -> float | None:
        p = _extension_origin(ext)
        if not p or not p.exists():
            return None
        try:
            return p.stat().st_mtime
        except Exception:
            return None

    async def _reload_one(self, ext: str) -> Tuple[str, str]:
        """Reload or load a single extension; returns (ext, status)."""
        try:
            if ext in self.bot.extensions:
                await self.bot.reload_extension(ext)
                return ext, "reloaded"
            else:
                await self.bot.load_extension(ext)
                return ext, "loaded"
        except commands.NoEntryPointError:
            return ext, "error: missing setup()"
        except Exception as e:
            return ext, f"error: {type(e).__name__}: {e}"

    # ---- commands ----
    @commands.command(name="extensions", aliases=["exts", "cogs"])
    @commands.guild_only()
    @_owner_or_server_owner_only()
    async def list_extensions(self, ctx: commands.Context):
        """List loaded vs discovered cogs."""
        importlib.invalidate_caches()
        discovered = _discover_extensions()
        loaded = self._current_loaded()

        loaded_list = [f"• {e}" for e in discovered if e in loaded]
        unloaded_list = [f"• {e}" for e in discovered if e not in loaded]

        parts = [
            "**Loaded**", *(loaded_list or ["(none)"]),
            "", "**Discovered (not loaded)**", *(unloaded_list or ["(none)"])
        ]
        await ctx.reply("\n".join(parts))

    @commands.command(name="reload", aliases=["rld"])
    @commands.guild_only()
    @_owner_or_server_owner_only()
    async def reload_cmd(self, ctx: commands.Context, *args: str):
        """
        ,reload all
        ,reload changed
        ,reload prune
        ,reload <name or pattern> [more...]
        examples: ,reload music tickets  |  ,reload anti*  |  ,reload *moderation
        """
        importlib.invalidate_caches()
        discovered = _discover_extensions()
        loaded = self._current_loaded()
        sub = (args[0].lower() if args else "")
        targets: List[str] = []

        if sub in {"all", "*"}:
            targets = discovered
        elif sub == "changed":
            changed: List[str] = []
            for ext in discovered:
                mt = self._get_mtime(ext)
                prev = self._ext_mtimes.get(ext)
                if mt is None:
                    continue
                if prev is None or mt > prev + 1e-6:
                    changed.append(ext)
            if not changed:
                return await ctx.reply("No changes detected.")
            targets = sorted(changed)
        elif sub == "prune":
            pruned = []
            for ext in sorted(loaded):
                p = _extension_origin(ext)
                if not p or not p.exists():
                    try:
                        await self.bot.unload_extension(ext)
                        pruned.append(ext)
                    except Exception:
                        pass
            for ext in pruned:
                self._ext_mtimes.pop(ext, None)
            return await ctx.reply("🧹 **Pruned:**\n" + ("\n".join(f"• {e}" for e in pruned) if pruned else "(nothing)"))
        else:
            if not args:
                return await ctx.reply("Usage: `,reload all | changed | prune | <name or pattern> [...]`")
            targets = self._match_targets(list(args), discovered)
            if not targets:
                return await ctx.reply("No matching cogs for: " + ", ".join(args))

        # Dedup + stable order; parents first (fewer import issues)
        seen = set()
        ordered = [t for t in targets if not (t in seen or seen.add(t))]
        ordered.sort(key=lambda x: x.count("."))

        results: List[Tuple[str, str]] = []
        for ext in ordered:
            res = await self._reload_one(ext)
            results.append(res)
            await asyncio.sleep(0)  # yield to event loop

        # Update mtimes for successes
        for ext, status in results:
            if status in {"reloaded", "loaded"}:
                mt = self._get_mtime(ext)
                if mt is not None:
                    self._ext_mtimes[ext] = mt

        ok = [f"• {e} — {st}" for e, st in results if st in {"reloaded", "loaded"}]
        bad = [f"• {e} — {st}" for e, st in results if st.startswith("error:")]

        parts = []
        if ok:
            await ctx.reply("ok.")



# =========================
# Your Admin cog (now with reload)
# =========================

class AdminCog(ReloadCommandsMixin, commands.Cog, name="Admin & Setup"):
    def __init__(self, bot: commands.Bot):
        super().__init__()   # init the mixin
        self.bot = bot

    @commands.group(name="prefix", invoke_without_command=True)
    @is_guild_owner_or_admin()
    async def prefix_group(self, ctx: commands.Context):
        pre = await self.bot.db.get_prefix(ctx.guild.id)
        await ctx.send(f"current prefix: `{pre}`")

    @prefix_group.command(name="set")
    @is_guild_owner_or_admin()
    async def prefix_set(self, ctx: commands.Context, prefix: str):
        await self.bot.db.set_prefix(ctx.guild.id, prefix)
        await ctx.send("ok")

    @commands.group(name="log", invoke_without_command=True)
    @is_guild_owner_or_admin()
    async def log_group(self, ctx: commands.Context):
        row = await self.bot.db.fetchrow(
            "SELECT security_log_channel_id, mod_log_channel_id FROM log_config WHERE guild_id=?",
            ctx.guild.id
        )
        if not row:
            await ctx.send("security: unset\nmod: unset")
            return
        s = row["security_log_channel_id"]; m = row["mod_log_channel_id"]
        await ctx.send(
            f"security: {('<#'+str(s)+'>') if s else 'unset'}\n"
            f"mod: {('<#'+str(m)+'>') if m else 'unset'}"
        )

    @log_group.command(name="security")
    @is_guild_owner_or_admin()
    async def log_security_set(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "INSERT INTO log_config(guild_id, security_log_channel_id) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET security_log_channel_id=excluded.security_log_channel_id",
            ctx.guild.id, channel.id
        )
        await ctx.send("ok")

    @log_group.command(name="mod")
    @is_guild_owner_or_admin()
    async def log_mod_set(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.db.execute(
            "INSERT INTO log_config(guild_id, mod_log_channel_id) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET mod_log_channel_id=excluded.mod_log_channel_id",
            ctx.guild.id, channel.id
        )
        await ctx.send("ok")

    @commands.command(name="setup")
    @is_guild_owner_or_admin()
    async def setup(self, ctx: commands.Context):
        guild = ctx.guild

        # Mute roles
        text_muted = discord.utils.get(guild.roles, name="Text Muted") or await guild.create_role(name="Text Muted", reason="Mute: text")
        image_muted = discord.utils.get(guild.roles, name="Image Muted") or await guild.create_role(name="Image Muted", reason="Mute: images")
        react_muted = discord.utils.get(guild.roles, name="Reaction Muted") or await guild.create_role(name="Reaction Muted", reason="Mute: reactions")

        # Log channels defaults
        security_log = discord.utils.get(guild.text_channels, name="security-log") or await guild.create_text_channel("security-log", reason="Security logs")
        mod_log = discord.utils.get(guild.text_channels, name="mod-log") or await guild.create_text_channel("mod-log", reason="Moderation logs")
        await self.bot.db.execute(
            "INSERT INTO log_config(guild_id, security_log_channel_id, mod_log_channel_id) VALUES(?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET security_log_channel_id=excluded.security_log_channel_id, mod_log_channel_id=excluded.mod_log_channel_id",
            guild.id, security_log.id, mod_log.id
        )

    @commands.command(name="bind", usage="bind staff @role")
    @is_guild_owner_or_admin()
    async def bind(self, ctx: commands.Context, kind: str, role: discord.Role):
        if kind.lower() != "staff":
            await ctx.send("only `staff` binding is supported: `,bind staff @role`.")
            return
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO staff_roles(guild_id, role_id) VALUES(?, ?)",
            ctx.guild.id, role.id
        )
        await ctx.send("ok")

    @commands.group(name="invoke", invoke_without_command=True)
    @is_guild_owner_or_admin()
    async def invoke_group(self, ctx: commands.Context):
        await ctx.send("usage: `,invoke (command) message <text>` or `,invoke (command) dm <text>`")

    @invoke_group.command(name="message")
    @is_guild_owner_or_admin()
    async def invoke_set_message(self, ctx: commands.Context, command: str, *, text: str):
        await self.bot.db.execute(
            "INSERT INTO invoke_messages(guild_id, command_name, invoke_msg) VALUES(?, ?, ?) "
            "ON CONFLICT(guild_id, command_name) DO UPDATE SET invoke_msg=excluded.invoke_msg",
            ctx.guild.id, command.lower(), text
        )
        await ctx.send("ok")

    @invoke_group.command(name="dm")
    @is_guild_owner_or_admin()
    async def invoke_set_dm(self, ctx: commands.Context, command: str, *, text: str):
        await self.bot.db.execute(
            "INSERT INTO invoke_messages(guild_id, command_name, dm_msg) VALUES(?, ?, ?) "
            "ON CONFLICT(guild_id, command_name) DO UPDATE SET dm_msg=excluded.dm_msg",
            ctx.guild.id, command.lower(), text
        )
        await ctx.send("ok")

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
