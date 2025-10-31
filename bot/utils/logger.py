from __future__ import annotations
from typing import Optional
import discord

async def _get_log_channels(bot, guild_id: int):
    row = await bot.db.fetchrow("SELECT security_log_channel_id, mod_log_channel_id FROM log_config WHERE guild_id=?", guild_id)
    if not row:
        return None, None
    return (int(row["security_log_channel_id"]) if row["security_log_channel_id"] else None,
            int(row["mod_log_channel_id"]) if row["mod_log_channel_id"] else None)

async def log_security(bot, guild: discord.Guild, content: str):
    sec_id, _ = await _get_log_channels(bot, guild.id)
    if not sec_id:
        return
    ch = guild.get_channel(sec_id)
    if isinstance(ch, discord.TextChannel):
        try: await ch.send(content)
        except Exception: pass

async def log_mod(bot, guild: discord.Guild, content: str):
    _, mod_id = await _get_log_channels(bot, guild.id)
    if not mod_id:
        return
    ch = guild.get_channel(mod_id)
    if isinstance(ch, discord.TextChannel):
        try: await ch.send(content)
        except Exception: pass
