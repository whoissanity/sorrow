from __future__ import annotations
from typing import Optional

import discord
from discord.ext import commands

from bot.utils.checks import is_guild_owner_or_admin

INTERFACE_TEXT = "Use the buttons below to manage your temporary voice channel."

async def _owned_temp_channel(bot: commands.Bot, member: discord.Member) -> Optional[discord.VoiceChannel]:
    """Return the temp voice channel owned by member (from DB), or the one they're in if they own it."""
    if not member or not member.guild:
        return None
    # Check current channel
    ch = member.voice.channel if member.voice else None
    if isinstance(ch, discord.VoiceChannel):
        row = await bot.db.fetchrow("SELECT owner_id FROM vm_channels WHERE channel_id=?", ch.id)
        if row and int(row["owner_id"]) == member.id:
            return ch
    # Otherwise from DB
    row = await bot.db.fetchrow("SELECT channel_id FROM vm_channels WHERE guild_id=? AND owner_id=?", member.guild.id, member.id)
    if row:
        c = member.guild.get_channel(int(row["channel_id"]))
        if isinstance(c, discord.VoiceChannel):
            return c
    return None

class VMInterfaceView(discord.ui.View):
    """Persistent VoiceMaster controller with safe callbacks (no global event parsing)."""
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

        # Buttons with stable custom_ids for persistence
        self._add_btn("Lock",        "vm:lock",        discord.ButtonStyle.secondary)
        self._add_btn("Unlock",      "vm:unlock",      discord.ButtonStyle.secondary)
        self._add_btn("Rename",      "vm:rename",      discord.ButtonStyle.primary)
        self._add_btn("Limit −",     "vm:limit_down",  discord.ButtonStyle.secondary)
        self._add_btn("Limit +",     "vm:limit_up",    discord.ButtonStyle.secondary)
        self._add_btn("Permit",      "vm:permit",      discord.ButtonStyle.success)

    def _add_btn(self, label: str, action: str, style: discord.ButtonStyle):
        btn = discord.ui.Button(
            label=label,
            style=style,
            custom_id=f"{action}:{self.guild_id}",
        )
        async def cb(interaction: discord.Interaction, action=action):
            await self._handle(interaction, action)
        btn.callback = cb
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.guild and interaction.guild.id == self.guild_id

    async def _handle(self, interaction: discord.Interaction, action: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        # Ownership check
        chan = await _owned_temp_channel(self.bot, interaction.user)
        if not chan:
            try:
                await interaction.response.send_message("you don't own a temporary voice channel.", ephemeral=True)
            except Exception:
                pass
            return

        # Actions
        try:
            if action == "vm:lock":
                await chan.set_permissions(interaction.guild.default_role, connect=False)
                await self._ok(interaction); return

            if action == "vm:unlock":
                await chan.set_permissions(interaction.guild.default_role, connect=None)
                await self._ok(interaction); return

            if action == "vm:rename":
                class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
                    def __init__(self, channel: discord.VoiceChannel):
                        super().__init__()
                        self.channel = channel
                        self.new_name = discord.ui.TextInput(label="Name", placeholder="New channel name", max_length=96)
                        self.add_item(self.new_name)
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        try:
                            await self.channel.edit(name=str(self.new_name))
                            await modal_interaction.response.send_message("ok.", ephemeral=True)
                        except Exception:
                            try: await modal_interaction.response.send_message("failed", ephemeral=True)
                            except Exception: pass
                await interaction.response.send_modal(RenameModal(chan)); return

            if action == "vm:limit_down":
                val = max(0, (chan.user_limit or 0) - 1)
                await chan.edit(user_limit=val)
                await self._ok(interaction); return

            if action == "vm:limit_up":
                val = min(99, (chan.user_limit or 0) + 1)
                await chan.edit(user_limit=val)
                await self._ok(interaction); return

            if action == "vm:permit":
                class PermitModal(discord.ui.Modal, title="Permit user to join"):
                    def __init__(self, channel: discord.VoiceChannel, guild: discord.Guild):
                        super().__init__()
                        self.channel = channel
                        self.guild = guild
                        self.text = discord.ui.TextInput(label="User ID or mention", placeholder="@user or 1234567890")
                        self.add_item(self.text)
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        s = str(self.text).strip()
                        if s.startswith("<@") and s.endswith(">"):
                            s = s.strip("<@!>")
                        uid = int(s) if s.isdigit() else None
                        target = self.guild.get_member(uid) if uid else None
                        if not target:
                            try: await modal_interaction.response.send_message("user not found", ephemeral=True)
                            except Exception: pass
                            return
                        try:
                            await self.channel.set_permissions(target, connect=True, view_channel=True)
                            await modal_interaction.response.send_message("ok.", ephemeral=True)
                        except Exception:
                            try: await modal_interaction.response.send_message("failed", ephemeral=True)
                            except Exception: pass
                await interaction.response.send_modal(PermitModal(chan, interaction.guild)); return

        except Exception:
            # Always try to reply to avoid "This interaction failed"
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("error.", ephemeral=True)
                else:
                    await interaction.followup.send("error.", ephemeral=True)
            except Exception:
                pass

    async def _ok(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("ok.", ephemeral=True)
            else:
                await interaction.followup.send("ok.", ephemeral=True)
        except Exception:
            pass

class VoiceMaster(commands.Cog, name="VoiceMaster"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="voicemaster", invoke_without_command=True)
    async def voicemaster(self, ctx: commands.Context):
        await ctx.send("subcommands: setup")

    @voicemaster.command(name="setup")
    @is_guild_owner_or_admin()
    async def vm_setup(self, ctx: commands.Context):
        guild = ctx.guild

        # Category
        category = discord.utils.get(guild.categories, name="VoiceMaster")
        if category is None:
            category = await guild.create_category("VoiceMaster", reason="VoiceMaster")

        # Join to Create VC
        jtc = discord.utils.get(guild.voice_channels, name="Join to Create")
        if jtc is None:
            jtc = await guild.create_voice_channel("Join to Create", category=category, reason="VoiceMaster join-to-create")

        # Interface channel (no chat/reactions for everyone)
        interface = discord.utils.get(guild.text_channels, name="interface")
        if interface is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, add_reactions=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=False, manage_channels=True, manage_messages=True)
            }
            interface = await guild.create_text_channel("interface", category=category, overwrites=overwrites, reason="VoiceMaster interface")
        else:
            try:
                await interface.set_permissions(guild.default_role, send_messages=False, add_reactions=False)
            except Exception:
                pass

        # Save IDs
        await self.bot.db.execute(
            "UPDATE guild_config SET vm_category_id=?, vm_jtc_id=?, vm_interface_id=? WHERE guild_id=?",
            category.id, jtc.id, interface.id, guild.id
        )

        # Post interface message with persistent view
        try:
            await interface.purge(limit=10)
        except Exception:
            pass
        view = VMInterfaceView(self.bot, guild.id)
        await interface.send(INTERFACE_TEXT, view=view)

        # Register persistent view for future interactions across restarts
        self.bot.add_view(VMInterfaceView(self.bot, guild.id))

        await ctx.send("ok.")

    # Create/cleanup temporary channels
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        row = await self.bot.db.fetchrow("SELECT vm_jtc_id, vm_category_id FROM guild_config WHERE guild_id=?", member.guild.id)
        if not row or not row["vm_jtc_id"] or not row["vm_category_id"]:
            return
        jtc_id = int(row["vm_jtc_id"]); cat_id = int(row["vm_category_id"])

        # Create on JTC join
        if after.channel and after.channel.id == jtc_id:
            category = member.guild.get_channel(cat_id)
            if not isinstance(category, discord.CategoryChannel):
                return
            name = f"{member.name}'s Channel"
            try:
                new_vc = await member.guild.create_voice_channel(name, category=category, rtc_region=None, reason="VoiceMaster create")
                await self.bot.db.execute("INSERT OR REPLACE INTO vm_channels(guild_id, channel_id, owner_id) VALUES(?, ?, ?)", member.guild.id, new_vc.id, member.id)
                await member.move_to(new_vc, reason="VoiceMaster move to new VC")
            except Exception:
                return

        # Cleanup empty temp VCs
        ch = before.channel
        if isinstance(ch, discord.VoiceChannel):
            r = await self.bot.db.fetchrow("SELECT 1 FROM vm_channels WHERE channel_id=?", ch.id)
            if r and len(ch.members) == 0:
                try:
                    await ch.delete(reason="VoiceMaster cleanup")
                except Exception:
                    pass
                finally:
                    await self.bot.db.execute("DELETE FROM vm_channels WHERE channel_id=?", ch.id)

    async def cog_load(self):
        # Re-register persistent views at startup (so buttons work after restarts)
        for g in self.bot.guilds:
            self.bot.add_view(VMInterfaceView(self.bot, g.id))

async def setup(bot: commands.Bot):
    cog = VoiceMaster(bot)
    await bot.add_cog(cog)
    # also register on load
    for g in bot.guilds:
        bot.add_view(VMInterfaceView(bot, g.id))
