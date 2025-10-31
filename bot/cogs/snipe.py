# bot/cogs/snipe.py
import collections
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

MAX_SNIPES_PER_CHANNEL = 50

class Snipe(commands.Cog, name="Snipe"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> deque of (author_name, content, created_at, attachments)
        self._snipes: dict[int, collections.deque] = {}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or not message.channel:
            return
        if message.author.bot:
            return
        if not message.content and not message.attachments:
            return
        dq = self._snipes.setdefault(message.channel.id, collections.deque(maxlen=MAX_SNIPES_PER_CHANNEL))
        att = message.attachments[0].url if message.attachments else None
        dq.appendleft((
            message.author.name,
            message.content,
            message.created_at or datetime.now(timezone.utc),
            att
        ))

    @commands.command(name="snipe", usage="[amount]")
    async def snipe(self, ctx: commands.Context, amount: Optional[int] = None):
        """Show recently deleted messages in this channel (default 1)."""
        dq = self._snipes.get(ctx.channel.id)
        if not dq:
            return await ctx.send("nothing to snipe.")
        n = 1 if amount is None else max(1, min(int(amount), 10))
        items = list(dq)[:n]
        lines = []
        for i, (author, content, created_at, att) in enumerate(items, 1):
            when = f"<t:{int(created_at.replace(tzinfo=timezone.utc).timestamp())}:R>"
            body = content or ""
            if att:
                body = (body + f"\n[attachment]({att})").strip()
            lines.append(f"**{i}. {author}** — {when}\n{body or '*no content*'}")
        await ctx.send("\n\n".join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
