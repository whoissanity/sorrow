# bot/cogs/help.py
import math
import discord
from discord.ext import commands

PAGE_SIZE = 8

def _cmd_line(prefix: str, cmd: commands.Command) -> str:
    usage = cmd.usage or ""
    params = f" {usage}" if usage else ""
    desc = (cmd.help or "—").strip()
    return f"`{prefix}{cmd.name}{params}`\n{desc}"

class Pager(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.index = 0

    async def _update(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.embeds)
        await self._update(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.embeds)
        await self._update(interaction)

class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, *, category: str | None = None):
        prefix = (ctx.prefix or ",").strip()

        if not category:
            embed = discord.Embed(
                title="Help",
                description=f"Use `{prefix}help <category>` for details.",
                color=0x2b2d31,
            )
            cats: dict[str, str] = {}
            for cog_name, cog in self.bot.cogs.items():
                cmds = [c for c in cog.get_commands() if not c.hidden]
                if cmds:
                    cats[cog_name] = ", ".join(f"`{c.name}`" for c in cmds)
            for name, cmds_list in sorted(cats.items()):
                embed.add_field(name=name, value=cmds_list, inline=False)
            embed.set_footer(text="sanity.")
            await ctx.send(embed=embed)
            return

        cog = (
            self.bot.cogs.get(category)
            or self.bot.cogs.get(category.title())
            or self.bot.cogs.get(category.capitalize())
        )
        if not cog:
            await ctx.send("category not found.")
            return

        cmds = [c for c in cog.get_commands() if not c.hidden]
        if not cmds:
            await ctx.send("no commands in this category.")
            return

        pages = math.ceil(len(cmds) / PAGE_SIZE)
        embeds: list[discord.Embed] = []
        for i in range(pages):
            chunk = cmds[i * PAGE_SIZE : (i + 1) * PAGE_SIZE]
            desc = "\n\n".join(_cmd_line(prefix, c) for c in chunk)
            e = discord.Embed(
                title=f"{cog.qualified_name} Commands ({i + 1}/{pages})",
                description=desc,
                color=0x2b2d31,
            )
            e.set_footer(text="made by sanity")
            embeds.append(e)

        view = Pager(embeds) if len(embeds) > 1 else None
        await ctx.send(embed=embeds[0], view=view)

async def setup(bot: commands.Bot):
    try:
        bot.remove_command("help")  # remove default to avoid clashes
    except Exception:
        pass
    await bot.add_cog(HelpCog(bot))
