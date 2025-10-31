# bot/cogs/vent.py
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

VENT_CHANNEL_ID = 1429128972293111890  # target vent/confession channel


class Vent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Ensure a simple per-guild counter table exists
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS vent_counters (
                guild_id    INTEGER PRIMARY KEY,
                next_id     INTEGER NOT NULL
            )
            """
        )

    async def _next_confession_id(self, guild_id: int) -> int:
        row = await self.bot.db.fetchrow(
            "SELECT next_id FROM vent_counters WHERE guild_id=?", guild_id
        )
        if not row:
            await self.bot.db.execute(
                "INSERT INTO vent_counters (guild_id, next_id) VALUES (?, ?)",
                guild_id,
                1,
            )
            conf_id = 1
        else:
            conf_id = int(row["next_id"])
        # bump for next time
        await self.bot.db.execute(
            "UPDATE vent_counters SET next_id=? WHERE guild_id=?",
            conf_id + 1,
            guild_id,
        )
        return conf_id

    @app_commands.command(
        name="vent", description="Send an anonymous confession to the vent channel."
    )
    @app_commands.describe(confession="What would you like to say? (anonymous)")
    async def vent(self, interaction: discord.Interaction, confession: str):
        """Anonymous confession. Posts an embed to the vent channel and starts a thread. Reply is ephemeral."""
        async def _reply(msg: str):
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

        if interaction.guild is None:
            return await _reply("server-only command.")

        confession = (confession or "").strip()
        if not confession:
            return await _reply("confession cannot be empty.")

        # fetch target channel
        ch = interaction.client.get_channel(VENT_CHANNEL_ID)
        if ch is None:
            try:
                ch = await interaction.client.fetch_channel(VENT_CHANNEL_ID)
            except Exception:
                ch = None
        if not isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
            return await _reply("vent channel not found or wrong type.")

        # Allocate confession number per guild
        try:
            num = await self._next_confession_id(interaction.guild.id)
        except Exception:
            return await _reply("counter error.")

        # Build anonymous embed (with number)
        text = confession
        if len(text) > 4000:
            text = text[:4000] + "…"

        embed = discord.Embed(
            title=f"#{num}",
            description=text,
            color=discord.Color.blurple(),
        )

        allowed = discord.AllowedMentions(everyone=False, roles=False, users=False)

        try:
            if isinstance(ch, discord.TextChannel):
                msg = await ch.send(embed=embed, allowed_mentions=allowed)
                # Thread name with number + snippet
                base = text.replace("\n", " ").strip()
                thread_name = (f"#{num} " + base)[:90] or f"#{num}"
                await msg.create_thread(name=thread_name, auto_archive_duration=1440)
            else:
                # Forum: create a forum post (thread) directly
                base = text.replace("\n", " ").strip()
                thread_name = (f"#{num} " + base)[:90] or f"#{num}"
                await ch.create_thread(
                    name=thread_name,
                    embed=embed,
                    allowed_mentions=allowed,
                )
        except discord.Forbidden:
            return await _reply("i'm missing permission to post or create threads in the vent channel.")
        except Exception as e:
            return await _reply(f"error: {e}")

        await _reply("ok")


async def setup(bot: commands.Bot):
    await bot.add_cog(Vent(bot))
