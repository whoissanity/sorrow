from __future__ import annotations
import logging
import discord
from discord.ext import commands

from bot.config import load_settings
from bot.db import Database
from bot.utils.log import setup_logging

COGS = [
    "bot.cogs.admin",
    "bot.cogs.moderation",
    "bot.cogs.voicemaster",
    "bot.cogs.greeter",
    "bot.cogs.boosterrole",
    "bot.cogs.fakeperms",
    "bot.cogs.music",
    "bot.cogs.tickets",
    "bot.cogs.utility",
    "bot.cogs.help",
    "bot.cogs.roletracker",
    "bot.cogs.misc",
    "bot.cogs.snipe",
    "bot.cogs.crypto",
    "bot.cogs.giveaways",
    "bot.cogs.leveling",
    "bot.cogs.vent",
    "bot.cogs.roles",
    "bot.cogs.antinuke",
]

logger = logging.getLogger(__name__)

class BleedStyleBot(commands.Bot):
    def __init__(self, **kwargs):
        intents = discord.Intents.all()
        intents.message_content = True
        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            help_command=None,  # use your custom help cog
            **kwargs,
        )
        self.db = Database("bot.db")

    async def setup_hook(self):
        # DB first
        await self.db.connect()
        await self.db.setup()

        # Load cogs
        for ext in COGS:
            try:
                await self.load_extension(ext)
            except Exception as e:
                logger.exception("Failed to load cog %s: %r", ext, e)

        # Dev: guild-only slash sync (fast)
        GUILD_ID = 1429118154302558380  # your test guild
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        # When ready for global rollout, also run:
        # await self.tree.sync()

    async def close(self):
        await super().close()
        await self.db.close()

    async def on_ready(self):
        logger.info("Logged in as %s (%s)", self.user, self.user.id)

    async def get_prefix(self, message: discord.Message):
        if not message.guild:
            return ","
        return await self.db.get_prefix(message.guild.id)

def main():
    setup_logging()
    settings = load_settings()
    bot = BleedStyleBot()
    bot.run(settings.token, log_handler=None)

if __name__ == "__main__":
    main()
