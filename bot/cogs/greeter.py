from __future__ import annotations
import json
import os
from typing import Dict, Any, Optional

import discord
from discord.ext import commands

CONFIG_PATH = "data/greetings.json"

def _load() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _ensure_guild(data: Dict[str, Any], gid: int) -> Dict[str, Any]:
    key = str(gid)
    if key not in data or not isinstance(data[key], dict):
        data[key] = {}
    g = data[key]
    g.setdefault("greetchannel_id", None)
    g.setdefault("leavechannel_id", None)
    g.setdefault("boosterchannel_id", None)
    g.setdefault("booster_role_id", None)
    return g

async def _fallback_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            return ch
    return None

# ---------- New/updated formatters ----------

def _join_message(member: discord.Member) -> str:
    # Plain message for joins
    return f"**{member.mention} Joined**"
    
def _welcome_embed(member: discord.Member) -> discord.Embed:
    count = member.guild.member_count  # humans+bots; use a humans-only variant if you prefer
    desc = (
        "**⤹﹒      🧸     ﹒    ﹒welc﹒﹏﹒\n"
        "       ✉﹒⌅﹒[book](https://discord.com/channels/1429118154302558380/1432387817513947167) "
        "﹒[roles](https://discord.com/channels/1429118154302558380/1429420602577387632) "
        "﹒[rep us](https://discord.com/channels/1429118154302558380/1429125304026267690/1432357053518581760) .ᐟ.ᐟ\n"
        f"﹒╰• ﹒we have {count:,} members!\n ```﹒thanks for joining!!!```\n"
        "**"
    )
    return discord.Embed(description=desc, colour=0x00B0F4)


def _booster_message(member: discord.Member) -> str:
    # Short plain message for boosters
    return f"**{member.mention} Boosted**"

def _booster_embed(member: discord.Member) -> discord.Embed:
    # Dynamic boost count if available; falls back to 17 if unknown
    count = getattr(member.guild, "premium_subscription_count", None)
    count_text = str(count) if isinstance(count, int) and count >= 0 else "17"
    desc = (
        f"{member.mention}\n"
        f"> ﹒  ✿  ⟢   we now have __{count_text}__ boosts\n"
        f"> ⏖﹒ claim your perks at <#1068840959649001499> _!_"
    )
    return discord.Embed(description=desc, colour=0x00B0F4)

# ---------- End formatters ----------

def _fmt_leave(member_like: discord.abc.User, guild_name: str) -> str:
    username = getattr(member_like, "name", "someone")
    
    
class Greeter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cfg: Dict[str, Any] = _load()

    @commands.command(name="greetchannel")
    @commands.has_guild_permissions(administrator=True)
    async def greetchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        g = _ensure_guild(self._cfg, ctx.guild.id)
        g["greetchannel_id"] = channel.id
        _save(self._cfg)
        await ctx.send("ok.")

    @commands.command(name="leavechannel")
    @commands.has_guild_permissions(administrator=True)
    async def leavechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        g = _ensure_guild(self._cfg, ctx.guild.id)
        g["leavechannel_id"] = channel.id
        _save(self._cfg)
        await ctx.send("ok.")

    @commands.command(name="boosterchannel")
    @commands.has_guild_permissions(administrator=True)
    async def boosterchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        g = _ensure_guild(self._cfg, ctx.guild.id)
        g["boosterchannel_id"] = channel.id
        _save(self._cfg)
        await ctx.send("ok.")

    @commands.command(name="boosterole")  # keeping your original command name
    @commands.has_guild_permissions(administrator=True)
    async def boosterole(self, ctx: commands.Context, role: discord.Role):
        g = _ensure_guild(self._cfg, ctx.guild.id)
        g["booster_role_id"] = role.id
        _save(self._cfg)
        await ctx.send("ok.")

    async def _send(self, guild: discord.Guild, channel_id: Optional[int], content: Optional[str] = None, embed: Optional[discord.Embed] = None):
        ch = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(ch, discord.TextChannel):
            ch = await _fallback_channel(guild)
        if ch is None:
            return
        try:
            # Send content and/or embed
            await ch.send(content=content, embed=embed)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        g = _ensure_guild(self._cfg, member.guild.id)
        await self._send(
            member.guild,
            g.get("greetchannel_id"),
            content=_join_message(member),
            embed=_welcome_embed(member),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        g = _ensure_guild(self._cfg, member.guild.id)
        await self._send(
            member.guild,
            g.get("leavechannel_id"),
            content=_fmt_leave(member, member.guild.name)
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        g = _ensure_guild(self._cfg, guild.id)
        booster_channel_id = g.get("boosterchannel_id")

        got_native_boost = False
        try:
            got_native_boost = (before.premium_since is None) and (after.premium_since is not None)
        except Exception:
            got_native_boost = False

        role_trigger = False
        rid = g.get("booster_role_id")
        if rid:
            before_ids = {r.id for r in getattr(before, "roles", [])}
            after_ids  = {r.id for r in getattr(after, "roles", [])}
            role_trigger = (rid not in before_ids) and (rid in after_ids)

        if got_native_boost or role_trigger:
            await self._send(
                guild,
                booster_channel_id,
                content=_booster_message(after),
                embed=_booster_embed(after),
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(Greeter(bot))
