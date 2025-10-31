import json
import os
import re
from typing import Optional, Union, Tuple, Dict

import discord
from discord.ext import commands

# ---- Helpers for emoji keys --------------------------------------------------

def emoji_key_from_partial(pe: discord.PartialEmoji) -> str:
    """Stable key: custom emoji -> ID, unicode -> the char(s)."""
    return str(pe.id) if pe.id else pe.name

def emoji_key_from_any(e: Union[str, discord.Emoji, discord.PartialEmoji]) -> str:
    if isinstance(e, discord.Emoji):
        return str(e.id)
    if isinstance(e, discord.PartialEmoji):
        return emoji_key_from_partial(e)
    # string: could be unicode or <:name:id>
    m = re.match(r"<a?:\w+:(\d+)>", e)
    if m:
        return m.group(1)  # custom emoji id
    return e  # unicode text

# ---- Storage layer -----------------------------------------------------------

class JSONStore:
    """
    data[guild_id][message_id][emoji_key] = role_id
    ids stored as strings for JSON safety.
    """
    def __init__(self, path: str = "reaction_roles.json"):
        self.path = path
        self.data: Dict[str, Dict[str, Dict[str, str]]] = {}
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

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def set_mapping(self, guild_id: int, message_id: int, emoji_key: str, role_id: int):
        g = self.data.setdefault(str(guild_id), {})
        m = g.setdefault(str(message_id), {})
        m[emoji_key] = str(role_id)
        self._save()

    def remove_mapping(self, guild_id: int, message_id: int, emoji_key: str) -> bool:
        g = self.data.get(str(guild_id), {})
        m = g.get(str(message_id), {})
        removed = m.pop(emoji_key, None) is not None
        if removed and not m:
            g.pop(str(message_id), None)
        if removed and not g:
            self.data.pop(str(guild_id), None)
        if removed:
            self._save()
        return removed

    def find_role(self, guild_id: int, message_id: int, emoji_key: str) -> Optional[int]:
        g = self.data.get(str(guild_id), {})
        m = g.get(str(message_id), {})
        rid = m.get(emoji_key)
        return int(rid) if rid is not None else None

# ---- Cog ---------------------------------------------------------------------

MESSAGE_LINK_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)"
)

class ReactionRoles(commands.Cog):
    """Reaction Role system: ,rr and ,rrremove"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = JSONStore()

    # --- utils ---

    async def parse_message_ref(
        self, ctx: commands.Context, ref: Optional[str]
    ) -> Tuple[discord.TextChannel, discord.Message]:
        """
        Accepts: message link, raw ID (current channel), or a reply (if ref is None).
        """
        if not ref and ctx.message.reference and ctx.message.reference.resolved:
            msg = ctx.message.reference.resolved
            if isinstance(msg, discord.Message):
                return msg.channel, msg

        if not ref:
            raise commands.BadArgument("Reply to a message or provide a message link/ID.")

        m = MESSAGE_LINK_RE.match(ref)
        if m:
            _, channel_id_s, message_id_s = m.groups()
            channel = ctx.guild.get_channel(int(channel_id_s)) if ctx.guild else None
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id_s))
            msg = await channel.fetch_message(int(message_id_s))
            return channel, msg

        # assume raw ID in current channel
        if not ctx.channel:
            raise commands.BadArgument("Cannot resolve channel for that message ID.")
        msg = await ctx.channel.fetch_message(int(ref))
        return ctx.channel, msg

    async def ensure_bot_can_assign(self, ctx: commands.Context, role: discord.Role):
        me = ctx.guild.me
        if not me.guild_permissions.manage_roles:
            raise commands.CheckFailure("I need the **Manage Roles** permission.")
        if role >= me.top_role:
            raise commands.CheckFailure(
                f"My top role must be higher than **{role.name}** to assign it."
            )

    # --- commands ---

    @commands.command(name="rr")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def rr(
        self,
        ctx: commands.Context,
        emoji: Union[discord.Emoji, discord.PartialEmoji, str],
        role: discord.Role,
        message_ref: str,
    ):
        """
        ,rr <emoji> <role mention|id> <message link|id>
        """
        await self.ensure_bot_can_assign(ctx, role)

        # Resolve target message
        channel, message = await self.parse_message_ref(ctx, message_ref)

        # Prepare reaction object
        to_react: Union[str, discord.PartialEmoji]
        if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)):
            to_react = emoji
        else:
            m = re.match(r"<a?:\w+:(\d+)>", emoji)
            if m:
                to_react = discord.PartialEmoji(animated="a" in emoji, id=int(m.group(1)), name=None)
            else:
                to_react = emoji  # unicode text

        # React
        try:
            await message.add_reaction(to_react)
        except discord.HTTPException:
            return await ctx.reply("Couldn't add that reaction (is the emoji valid/available here?).")

        # Store mapping
        key = emoji_key_from_any(emoji)
        self.store.set_mapping(ctx.guild.id, message.id, key, role.id)

        await ctx.reply(
            f"✅ Reaction role set: react with **{emoji}** on [this message]({message.jump_url}) "
            f"to receive **@{role.name}**."
        )

    @commands.command(name="rrremove")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def rrremove(
        self,
        ctx: commands.Context,
        emoji: Union[discord.Emoji, discord.PartialEmoji, str],
        message_ref: Optional[str] = None,
    ):
        """
        ,rrremove <emoji>
        (Run it as a reply to the target message. You may also pass a message link|id.)
        """
        key = emoji_key_from_any(emoji)

        # Resolve target message (prefer reply)
        channel: Optional[discord.TextChannel] = None
        message: Optional[discord.Message] = None
        try:
            channel, message = await self.parse_message_ref(ctx, message_ref)
        except commands.BadArgument:
            # If no ref provided and not a reply, guide the user
            return await ctx.reply(
                "Reply to the target message or provide a message link/ID: "
                "`,rrremove <emoji> <message link|id>`"
            )

        removed = self.store.remove_mapping(ctx.guild.id, message.id, key)
        if not removed:
            return await ctx.reply("No reaction role mapping found for that emoji on that message.")

        await ctx.reply("✅ Reaction role mapping removed.")

    # --- listeners ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        key = emoji_key_from_partial(payload.emoji)
        role_id = self.store.find_role(payload.guild_id, payload.message_id, key)
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id) or await self.bot.fetch_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
        if member.bot:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        if role in member.roles:
            return

        try:
            await member.add_roles(role, reason=f"Reaction role on message {payload.message_id}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        key = emoji_key_from_partial(payload.emoji)
        role_id = self.store.find_role(payload.guild_id, payload.message_id, key)
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id) or await self.bot.fetch_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
        if member.bot:
            return

        role = guild.get_role(role_id)
        if not role or role not in member.roles:
            return

        try:
            await member.remove_roles(role, reason=f"Reaction role removed on message {payload.message_id}")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
