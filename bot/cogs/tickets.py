from __future__ import annotations
import os, json, io
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.ui import View, Button

import chat_exporter  # from py-discord-html-transcripts
from bot.utils.logger import log_mod

CONFIG_PATH = "data/tickets.json"
TRANSCRIPT_DIR = "transcript"   # save under ./transcript/<guild_id>/


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


def _ensure_guild(cfg: Dict[str, Any], gid: int) -> Dict[str, Any]:
    key = str(gid)
    if key not in cfg or not isinstance(cfg[key], dict):
        cfg[key] = {}
    g = cfg[key]
    g.setdefault("staff_role_ids", [])
    g.setdefault("category_id", None)
    g.setdefault("transcript_channel_id", None)
    # default panel still present, but we'll render only an emoji button
    g.setdefault(
        "panels",
        {
            "1": {
                "text": "Press the button to open a support ticket.",
                "button_label": "Open Ticket",
                "emoji": "🎫",
            }
        },
    )
    g.setdefault("panel_channels", {})  # {panel_id: [channel_ids]}
    return g


def _has_any_role(member: discord.Member, role_ids: List[int]) -> bool:
    if not role_ids:
        return False
    ids = set(role_ids)
    return any((r.id in ids) for r in member.roles)


class TicketPanelView(View):
    """Single-button persistent view."""

    def __init__(self, cog: "Tickets", guild_id: int, panel_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.panel_id = panel_id
        g = _ensure_guild(self.cog.cfg, guild_id)
        pdata = g["panels"].get(panel_id, {})
        emoji_str = pdata.get("emoji") or "🎫"

        # Parse custom/animated emoji like <:name:id> or <a:name:id>
        emoji_obj: Optional[discord.PartialEmoji]
        try:
            emoji_obj = discord.PartialEmoji.from_str(emoji_str)
        except Exception:
            emoji_obj = None

        btn = Button(
            # No label per request — emoji-only button
            label=None,
            emoji=emoji_obj if (emoji_obj and emoji_obj.id) else emoji_str,
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_panel:{guild_id}:{panel_id}",
        )

        async def on_click(interaction: discord.Interaction):
            await self.cog._handle_open(interaction, guild_id, panel_id)

        btn.callback = on_click
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.guild and interaction.guild.id == self.guild_id


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg: Dict[str, Any] = _load()

    # ---- helpers ----
    def _is_staff(self, member: discord.Member) -> bool:
        g = _ensure_guild(self.cfg, member.guild.id)
        return member.guild_permissions.administrator or _has_any_role(
            member, [int(x) for x in g["staff_role_ids"]]
        )

    async def _panel_view(self, guild_id: int, panel_id: str) -> TicketPanelView:
        return TicketPanelView(self, guild_id, panel_id)

    async def _ensure_persistent_views(self):
        for gid_str, _ in list(self.cfg.items()):
            try:
                gid = int(gid_str)
            except Exception:
                continue
            panels = _ensure_guild(self.cfg, gid)["panels"]
            for pid in list(panels.keys()):
                self.bot.add_view(await self._panel_view(gid, pid))

    async def _transcript_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        g = _ensure_guild(self.cfg, guild.id)
        tcid = g.get("transcript_channel_id")
        ch = guild.get_channel(int(tcid)) if tcid else None
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _category(self, guild: discord.Guild) -> Optional[discord.CategoryChannel]:
        g = _ensure_guild(self.cfg, guild.id)
        cid = g.get("category_id")
        ch = guild.get_channel(int(cid)) if cid else None
        return ch if isinstance(ch, discord.CategoryChannel) else None

    async def _handle_open(
        self, interaction: discord.Interaction, guild_id: int, panel_id: str
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        guild = interaction.guild
        member: discord.Member = interaction.user

        cat = await self._category(guild)
        if not cat:
            try:
                await interaction.response.send_message(
                    "ticket category not configured.", ephemeral=True
                )
            except Exception:
                pass
            return

        name = f"ticket-{member.name}".lower().replace(" ", "-")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        gcfg = _ensure_guild(self.cfg, guild.id)
        for rid in gcfg["staff_role_ids"]:
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        try:
            ch = await guild.create_text_channel(
                name=name, category=cat, overwrites=overwrites, reason="Ticket open"
            )
        except Exception:
            try:
                await interaction.response.send_message(
                    "failed to create ticket.", ephemeral=True
                )
            except Exception:
                pass
            return

        await self.bot.db.execute(
            "INSERT OR REPLACE INTO tickets(guild_id, channel_id, opener_id) VALUES(?, ?, ?)",
            guild.id,
            ch.id,
            member.id,
        )

        try:
            # ticket thread notice should @mention the opener (not their name)
            await ch.send(
                f"ticket opened by {member.mention}. use `,close <reason>` to close."
            )
            # ephemeral confirmation should say "ticket created #channel>"
            text = f"ticket created <#{ch.id}>"
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            pass

        await log_mod(
            self.bot, guild, f"ticket open: {member} -> <#{ch.id}>"
        )

    async def _save_html_transcript(self, channel: discord.TextChannel) -> Optional[str]:
        """
        Export HTML via py-discord-html-transcripts (module name: chat_exporter).
        Returns saved filesystem path or None.
        """
        try:
            os.makedirs(
                os.path.join(TRANSCRIPT_DIR, str(channel.guild.id)), exist_ok=True
            )
            html_str = await chat_exporter.export(
                channel,
                limit=None,
                tz_info="UTC",
                military_time=True,
                bot=self.bot,  # lets exporter resolve members no longer in guild
                guild=channel.guild,  # helps some forks; safe here
            )
            if html_str is None:
                return None
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            filename = f"{channel.name}-{channel.id}-{ts}.html"
            path = os.path.join(TRANSCRIPT_DIR, str(channel.guild.id), filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_str)
            return path
        except Exception:
            return None

    # ---- commands ----
    @commands.command(name="ticketsetup")
    @commands.has_guild_permissions(administrator=True)
    async def ticketsetup(self, ctx: commands.Context):
        guild = ctx.guild
        g = _ensure_guild(self.cfg, guild.id)

        cat = await self._category(guild)
        if not cat:
            cat = await guild.create_category("Tickets", reason="Ticket System")
            g["category_id"] = cat.id

        tlog = await self._transcript_channel(guild)
        if not tlog:
            tlog = await guild.create_text_channel(
                "ticket-transcripts", reason="Ticket Transcripts"
            )
            g["transcript_channel_id"] = tlog.id

        if not g["staff_role_ids"]:
            staff = discord.utils.get(guild.roles, name="Staff")
            if staff:
                g["staff_role_ids"] = [staff.id]

        panels = g["panels"]
        if "1" not in panels:
            panels["1"] = {
                "text": "Press the button to open a support ticket.",
                "button_label": "Open Ticket",
                "emoji": "🎫",
            }
        _save(self.cfg)

        # Send panel 1 (emoji-only button, no text message)
        view = await self._panel_view(guild.id, "1")
        await ctx.send(view=view)
        chlist = g["panel_channels"].setdefault("1", [])
        if ctx.channel.id not in chlist:
            chlist.append(ctx.channel.id)
            _save(self.cfg)

        # Register persistent views for future restarts
        await self._ensure_persistent_views()
        await ctx.send("ok.")

    @commands.command(name="panel")
    @commands.has_guild_permissions(administrator=True)
    async def panel(
        self, ctx: commands.Context, ids: str, channel: Optional[discord.TextChannel] = None
    ):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        panels = g["panels"]
        target = channel or ctx.channel
        for pid in [p.strip() for p in ids.split(",") if p.strip()]:
            if pid not in panels:
                panels[pid] = {
                    "text": "Press the button to open a support ticket.",
                    "button_label": "Open Ticket",
                    "emoji": "🎫",
                }
            _save(self.cfg)
            view = await self._panel_view(ctx.guild.id, pid)
            # Send only the emoji button (no text)
            await target.send(view=view)
            lst = g["panel_channels"].setdefault(pid, [])
            if target.id not in lst:
                lst.append(target.id)
            _save(self.cfg)
        await self._ensure_persistent_views()
        await ctx.send("ok.")

    @commands.command(name="panelcreate")
    @commands.has_guild_permissions(administrator=True)
    async def panelcreate(
        self,
        ctx: commands.Context,
        panel_id: str,
        *,
        text: str = "Press the button to open a support ticket.",
    ):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        panels = g["panels"]
        if panel_id in panels:
            panels[panel_id]["text"] = text
        else:
            panels[panel_id] = {"text": text, "button_label": "Open Ticket", "emoji": "🎫"}
        _save(self.cfg)
        await self._ensure_persistent_views()
        await ctx.send("ok.")

    @commands.group(name="ticketstaff", invoke_without_command=True)
    @commands.has_guild_permissions(administrator=True)
    async def ticketstaff(self, ctx: commands.Context):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        ids = [f"<@&{rid}>" for rid in g["staff_role_ids"]]
        await ctx.send("staff: " + (", ".join(ids) if ids else "none"))

    @ticketstaff.command(name="add")
    @commands.has_guild_permissions(administrator=True)
    async def ticketstaff_add(self, ctx: commands.Context, role: discord.Role):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        if role.id not in g["staff_role_ids"]:
            g["staff_role_ids"].append(role.id)
            _save(self.cfg)
        await ctx.send("ok.")

    @ticketstaff.command(name="remove")
    @commands.has_guild_permissions(administrator=True)
    async def ticketstaff_remove(self, ctx: commands.Context, role: discord.Role):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        g["staff_role_ids"] = [rid for rid in g["staff_role_ids"] if rid != role.id]
        _save(self.cfg)
        await ctx.send("ok.")

    # NEW: set an emoji for a panel (supports custom & animated)
    @commands.command(name="panelemoji")
    @commands.has_guild_permissions(administrator=True)
    async def panelemoji(self, ctx: commands.Context, panel_id: str, emoji: str):
        g = _ensure_guild(self.cfg, ctx.guild.id)
        panels = g["panels"]
        if panel_id not in panels:
            panels[panel_id] = {
                "text": "Press the button to open a support ticket.",
                "button_label": "Open Ticket",
                "emoji": "🎫",
            }
        panels[panel_id]["emoji"] = emoji  # can be unicode or <:name:id> or <a:name:id>
        _save(self.cfg)
        await self._ensure_persistent_views()
        await ctx.send("ok.")

    # ... (keep the exact file I gave you previously, but change the close() and add delete() as below)

    @commands.command(name="close")
    async def close(self, ctx: commands.Context, *, reason: str = "No reason provided"):
        if not isinstance(ctx.author, discord.Member):
            return
        if not self._is_staff(ctx.author):
            return await ctx.send("only staff can close tickets.")
        ch = ctx.channel
        if not isinstance(ch, discord.TextChannel):
            return

        row = await self.bot.db.fetchrow(
            "SELECT opener_id, closed FROM tickets WHERE channel_id=?", ch.id
        )
        if not row or int(row["closed"]) == 1:
            return await ctx.send("not a ticket channel.")

        opener_id = int(row["opener_id"])
        opener = ch.guild.get_member(opener_id) or await self.bot.fetch_user(opener_id)

        # ---- Build transcript in memory (no disk writes) ----
        try:
            html_str = await chat_exporter.export(
                ch,
                limit=None,
                tz_info="UTC",
                military_time=False,
                bot=self.bot,
                guild=ctx.guild,
            )
            if not html_str:
                return await ctx.send("failed to make transcript.")

            filename = f"transcript-{ch.id}.html"
            # Two separate buffers: one for log channel, one for DM
            bio_log = io.BytesIO(html_str.encode("utf-8"))
            bio_user = io.BytesIO(html_str.encode("utf-8"))
            file_for_log = discord.File(bio_log, filename=filename)
            file_for_user = discord.File(bio_user, filename=filename)
        except Exception:
            return await ctx.send("failed to make transcript.")

        # ---- Mark closed in DB ----
        await self.bot.db.execute(
            "UPDATE tickets SET closed=1, closed_by=?, close_reason=?, closed_at=CURRENT_TIMESTAMP WHERE channel_id=?",
            ctx.author.id,
            reason,
            ch.id,
        )

        # ---- Lock channel & rename ----
        try:
            await ch.set_permissions(ctx.guild.default_role, send_messages=False)
            await ch.edit(name=f"closed-{ch.name[:80]}")
        except Exception:
            pass

        # ---- Send transcript to transcript log channel ----
        tlog = await self._transcript_channel(ctx.guild)
        if tlog:
            try:
                msg = (
                    f"ticket closed: opener=**{getattr(opener, 'name', opener_id)}**, "
                    f"closed by **{ctx.author.name}**, reason: {reason}"
                )
                await tlog.send(msg, file=file_for_log)
            except Exception:
                pass

        # ---- DM transcript to opener ----
        try:
            if isinstance(opener, (discord.User, discord.Member)):
                await opener.send(
                    f"Your ticket in **{ctx.guild.name}** was closed. Reason: {reason}",
                    file=file_for_user,
                )
        except Exception:
            pass

        await log_mod(
            self.bot, ctx.guild, f"ticket closed: <#{ch.id}> by {ctx.author} | {reason}"
        )
        await ctx.send("ok.")

    @commands.command(name="delete")
    async def delete_ticket(self, ctx: commands.Context):
        # Only staff may delete a ticket channel
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            return await ctx.send("only staff can delete tickets.")
        ch = ctx.channel
        if not isinstance(ch, discord.TextChannel):
            return
        r = await self.bot.db.fetchrow(
            "SELECT 1 FROM tickets WHERE channel_id=?", ch.id
        )
        if not r:
            return await ctx.send("not a ticket channel.")
        try:
            await ctx.send("ok.")
            await ch.delete(reason=f"Ticket deleted by {ctx.author}")
        except Exception:
            pass

    @commands.command(name="transcript", usage="[#channel] [@user]")
    @commands.guild_only()
    async def make_transcript(self, ctx: commands.Context):
        """
        Create a transcript for the chosen channel and DM it to the chosen user.
        Defaults: channel=current channel, user=you.
        Forms:
          ,transcript
          ,transcript @user
          ,transcript #channel
          ,transcript #channel @user
        """
        # Decide channel & recipient from message mentions
        ch = None
        if ctx.message.channel_mentions:
            # first mentioned channel
            ch = ctx.message.channel_mentions[0]
        if ch is None:
            if isinstance(ctx.channel, discord.TextChannel):
                ch = ctx.channel
            else:
                return await ctx.send("not a text channel.")

        # Recipient: first mentioned member; else author
        target = ctx.author
        if ctx.message.mentions:
            target = ctx.message.mentions[0]

        # Export & save
        saved_path = await self._save_html_transcript(ch)
        if not saved_path or not os.path.exists(saved_path):
            # fallback: try direct export and stream
            try:
                html_str = await chat_exporter.export(
                    ch,
                    limit=None,
                    tz_info="UTC",
                    military_time=True,
                    bot=self.bot,
                    guild=ctx.guild,
                )
                if not html_str:
                    return await ctx.send("failed to export transcript.")
                bio = io.BytesIO(html_str.encode("utf-8"))
                bio.seek(0)
                try:
                    await target.send(
                        f"Transcript for **#{ch.name}**",
                        file=discord.File(bio, filename=f"transcript-{ch.id}.html"),
                    )
                    return await ctx.send("ok.")
                except Exception:
                    return await ctx.send("could not DM that user.")
            except Exception:
                return await ctx.send("failed to export transcript.")

        # DM the saved file
        try:
            await target.send(
                f"Transcript for **#{ch.name}**",
                file=discord.File(
                    saved_path, filename=os.path.basename(saved_path)
                ),
            )
            await ctx.send("ok.")
        except Exception:
            await ctx.send("could not DM that user.")

    @commands.command(name="rename", usage="<new-name>")
    @commands.guild_only()
    async def rename_ticket(self, ctx: commands.Context, *, new_name: str):
        """
        Rename the current ticket channel. Staff-only and must be a ticket channel
        known to the DB.
        """
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            return await ctx.send("only staff can rename tickets.")
        if not isinstance(ctx.channel, discord.TextChannel):
            return await ctx.send("not a ticket channel.")

        # Verify it's a ticket in DB
        row = await self.bot.db.fetchrow(
            "SELECT 1 FROM tickets WHERE channel_id=?", ctx.channel.id
        )
        if not row:
            return await ctx.send("not a ticket channel.")

        # Slug the new name, keep it reasonably short
        safe = new_name.strip().lower().replace(" ", "-")
        safe = "".join(c for c in safe if c.isalnum() or c in "-_")[:90] or "ticket"

        try:
            await ctx.channel.edit(name=safe, reason=f"ticket rename by {ctx.author}")
            await ctx.send("ok.")
        except Exception as e:
            await ctx.send(f"error: {e}")


async def setup(bot: commands.Bot):
    cog = Tickets(bot)
    await bot.add_cog(cog)
    # re-register persistent panel views so buttons work after restarts
    await cog._ensure_persistent_views()
