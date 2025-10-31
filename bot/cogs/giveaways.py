

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Set

import discord
from discord.ext import commands

# -------- Time helpers --------

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration_to_seconds(text: str) -> int:
    s = text.strip().lower()
    if not s:
        raise ValueError("Empty duration.")
    if s.isdigit():
        return int(s) * 60  # plain integer means minutes
    total = 0
    for num, unit in re.findall(r"(\d+)\s*([wdhms])", s):
        total += int(num) * _UNITS[unit]
    if total <= 0:
        raise ValueError(f"Invalid or zero duration: '{text}'")
    return total


def discord_ts(ts: int, style: str = "R") -> str:
    return f"<t:{ts}:{style}>"


class Giveaways(commands.Cog):
    DATA_FILE = "giveaways_data.json"
    # Default reaction = your custom emoji (must exist where the giveaway is run)
    DEFAULT_REACTION_STR = "<:black_blackstar:1431280327531430019>"
    DEFAULT_REACTION_ID = 1431280327531430019

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._save_lock = asyncio.Lock()
        self.data: Dict[str, Any] = {"guilds": {}}
        self._end_tasks: Dict[int, asyncio.Task] = {}
        self._claim_tasks: Dict[str, asyncio.Task] = {}

    # -------- Persistence --------

    def _ensure_guild(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.data["guilds"]:
            self.data["guilds"][gid] = {
                "claimtimes": {},               # role_id -> seconds (no default)
                "giveaways": {},
                "latest_by_channel": {},
            }
        return self.data["guilds"][gid]

    async def _save(self) -> None:
        async with self._save_lock:
            tmp = self.DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.DATA_FILE)

    def _load(self) -> None:
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                # keep defaults if storage corrupted
                pass

    async def cog_load(self) -> None:
        self._load()
        now = int(time.time())
        for _, gdata in self.data.get("guilds", {}).items():
            for msg_id_str, gw in gdata.get("giveaways", {}).items():
                msg_id = int(msg_id_str)
                if not gw.get("ended"):
                    await self._schedule_end_task(msg_id, gw)
                # Re-arm any pending claim deadlines
                for uid_str, deadline in gw.get("claim_deadlines", {}).items():
                    if deadline and deadline > now:
                        await self._schedule_claim_task(msg_id, int(uid_str), deadline)

    async def cog_unload(self) -> None:
        for t in list(self._end_tasks.values()):
            t.cancel()
        for t in list(self._claim_tasks.values()):
            t.cancel()

    # -------- Internal helpers --------

    def _get_giveaway(self, guild_id: int, message_id: int) -> Optional[Dict[str, Any]]:
        g = self._ensure_guild(guild_id)
        return g["giveaways"].get(str(message_id))

    def _set_latest_for_channel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        g = self._ensure_guild(guild_id)
        g["latest_by_channel"][str(channel_id)] = str(message_id)

    def _get_latest_for_channel(self, guild_id: int, channel_id: int) -> Optional[int]:
        g = self._ensure_guild(guild_id)
        mid = g["latest_by_channel"].get(str(channel_id))
        return int(mid) if mid else None

    def _get_guild_id_by_msg(self, message_id: int) -> int:
        for gid, gdata in self.data.get("guilds", {}).items():
            if str(message_id) in gdata.get("giveaways", {}):
                return int(gid)
        raise KeyError("Guild not found for message_id")

    async def _schedule_end_task(self, message_id: int, gw: Dict[str, Any]) -> None:
        prev = self._end_tasks.pop(message_id, None)
        if prev:
            prev.cancel()

        async def runner():
            try:
                delay = max(0, gw["end_ts"] - int(time.time()))
                await asyncio.sleep(delay)
                await self._end_giveaway(gw)
            except asyncio.CancelledError:
                return
            except Exception as e:
                channel = self.bot.get_channel(gw["channel_id"])
                if channel:
                    await channel.send(f"⚠️ Error ending giveaway `{message_id}`: {e}")

        self._end_tasks[message_id] = asyncio.create_task(runner())

    async def _schedule_claim_task(self, message_id: int, user_id: int, deadline_ts: int) -> None:
        key = f"{message_id}:{user_id}"
        prev = self._claim_tasks.pop(key, None)
        if prev:
            prev.cancel()

        async def runner():
            try:
                delay = max(0, deadline_ts - int(time.time()))
                await asyncio.sleep(delay)

                guild_id = self._get_guild_id_by_msg(message_id)
                gw = self._get_giveaway(guild_id, message_id)
                if not gw:
                    return

                # Ensure deadline is still the same
                current = gw.get("claim_deadlines", {}).get(str(user_id))
                if current != deadline_ts:
                    return

                # Post at most once per (message_id, user_id)
                notified = gw.setdefault("claim_notified", {})  # user_id (str) -> True
                if notified.get(str(user_id)):
                    return
                # Mark as notified BEFORE sending (idempotent against races)
                notified[str(user_id)] = True
                # Remove deadline to avoid rescheduling on reload
                gw["claim_deadlines"].pop(str(user_id), None)
                await self._save()

                channel = self.bot.get_channel(gw["channel_id"]) or await self.bot.fetch_channel(gw["channel_id"])
                await self._save()
            except asyncio.CancelledError:
                return
            except Exception as e:
                try:
                    channel = self.bot.get_channel(gw["channel_id"]) or await self.bot.fetch_channel(gw["channel_id"])  # type: ignore[name-defined]
                except Exception:
                    channel = None
                if channel:
                    await channel.send(f"⚠️ Error handling claim window for `{message_id}`: {e}")

        self._claim_tasks[key] = asyncio.create_task(runner())

    def _claim_seconds_for_member(self, guild: discord.Guild, member: Optional[discord.Member]) -> Optional[int]:
        """
        Sum claim times for ALL configured roles the member has.
        No configured roles -> None (no claim window).
        """
        if not member:
            return None
        gdata = self._ensure_guild(guild.id)
        mapping = gdata["claimtimes"]  # role_id (str) -> seconds

        total = 0
        matched = False
        for role in getattr(member, "roles", []):
            secs = mapping.get(str(role.id))
            if secs is not None:
                total += int(secs)
                matched = True

        return total if matched else None

    def _render_custom_embed(self, host_id: int, end_ts: int, winners_count: int, reward: str) -> discord.Embed:
        duration_txt = discord_ts(end_ts, "R")
        winners_line = f"<a:991421catyawn:1431281029519511562> \u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0 ❐．winners: {winners_count}"
        host_line = f"﹒<:black_blackstar:1431280327531430019> ﹒⏖ ho**st** : __<@{host_id}>__"
        desc = f"◟ㆍ✧﹒ __en__ds {duration_txt}\n{winners_line}\n{host_line}"
        emb = discord.Embed(description=desc, colour=0x00B0F4)
        emb.title = f"🧸　﹒{reward}"
        return emb

    async def _update_gw_message(self, guild: discord.Guild, gw: Dict[str, Any]) -> None:
        channel = self.bot.get_channel(gw["channel_id"]) or await self.bot.fetch_channel(gw["channel_id"])
        msg: discord.Message = await channel.fetch_message(gw["message_id"])
        await msg.edit(embed=self._render_custom_embed(gw["host_id"], gw["end_ts"], gw["winners_count"], gw["reward"]))

    async def _fetch_entrants(self, channel_id: int, message_id: int, emoji_str: str, emoji_id: Optional[int]) -> List[int]:
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        message: discord.Message = await channel.fetch_message(message_id)

        target_reaction: Optional[discord.Reaction] = None
        for r in message.reactions:
            e = r.emoji
            if emoji_id is not None:
                if hasattr(e, "id") and e.id == emoji_id:
                    target_reaction = r
                    break
            else:
                if isinstance(e, str) and e == emoji_str:
                    target_reaction = r
                    break

        if not target_reaction:
            return []

        entrants: Set[int] = set()
        async for user in target_reaction.users(limit=None):
            if not user.bot:
                entrants.add(user.id)
        return list(entrants)

    async def _end_giveaway(self, gw: Dict[str, Any]) -> None:
        if gw.get("ended"):
            return

        channel = self.bot.get_channel(gw["channel_id"]) or await self.bot.fetch_channel(gw["channel_id"])

        try:
            entrants = await self._fetch_entrants(gw["channel_id"], gw["message_id"], gw["emoji_str"], gw["emoji_id"])
        except discord.Forbidden:
            await channel.send("⚠️ I need **Read Message History** to fetch entrants.")
            entrants = []
        except Exception as e:
            await channel.send(f"⚠️ Failed to fetch entrants: {e}")
            entrants = []

        valid_entrants = [uid for uid in entrants if uid != self.bot.user.id]
        already = set(gw.get("winner_ids", []))
        pool = [u for u in valid_entrants if u not in already]
        k = min(gw["winners_count"], len(pool))
        winners: List[int] = random.sample(pool, k) if k > 0 else []

        gw["ended"] = True
        gw.setdefault("winner_ids", []).extend(winners)

        try:
            await self._update_gw_message(channel.guild, gw)
        except Exception:
            pass

        if not winners:
            await channel.send("No valid entrants. No winners this time.")
            await self._save()
            return

        guild: discord.Guild = channel.guild
        now = int(time.time())
        gw.setdefault("claim_deadlines", {})
        gw.setdefault("claim_notified", {})

        for uid in winners:
            await channel.send(f"**<@{uid}> has won `{gw['reward']}`**!")

            try:
                member = guild.get_member(uid) or await guild.fetch_member(uid)
            except Exception:
                member = None

            claim_seconds = self._claim_seconds_for_member(guild, member)

            if claim_seconds is not None:
                claim_until = now + max(1, claim_seconds)
                gw["claim_deadlines"][str(uid)] = claim_until
                # Ensure notified flag is clear for this user
                gw["claim_notified"].pop(str(uid), None)
                await channel.send(f"open <#1429125288142307460> {discord_ts(claim_until, 'R')} to dm claim!")
                await self._schedule_claim_task(gw["message_id"], uid, claim_until)

        await self._save()

    # -------- Commands --------

    @commands.command(name="cl")
    @commands.has_permissions(manage_guild=True)
    async def set_claimtime(self, ctx: commands.Context, role: str, claimtime: str):
        gdata = self._ensure_guild(ctx.guild.id)
        try:
            seconds = parse_duration_to_seconds(claimtime)
        except ValueError as e:
            return await ctx.send(f"❌ Invalid claim time: {e}")

        try:
            role_obj = await commands.RoleConverter().convert(ctx, role)
        except commands.BadArgument:
            return await ctx.send("❌ Could not resolve that role. Mention it or provide its exact name/ID.")

        gdata["claimtimes"][str(role_obj.id)] = seconds
        await self._save()
        return await ctx.send(f"✅ Claim time for {role_obj.mention} set.")

    @commands.command(name="gwc")
    @commands.has_permissions(manage_guild=True)
    async def start_giveaway(
        self,
        ctx: commands.Context,
        duration: str,
        *,
        tail: Optional[str] = None,
    ):
        """
        ,gwc <duration> <reward...>                -> defaults winners=1
        ,gwc <duration> <winners> <reward...>      -> explicit winners
        """
        try:
            seconds = parse_duration_to_seconds(duration)
        except ValueError as e:
            return await ctx.send(f"❌ Invalid duration: {e}")

        if not tail or not tail.strip():
            return await ctx.send("❌ Please provide a reward (and optional winners).")

        parts = tail.strip().split()
        if parts[0].isdigit():
            winners = int(parts[0])
            reward = " ".join(parts[1:]).strip()
        else:
            winners = 1
            reward = " ".join(parts).strip()

        if winners <= 0:
            return await ctx.send("❌ Winners must be a positive integer.")
        if not reward:
            return await ctx.send("❌ Please provide a reward name/description.")

        end_ts = int(time.time()) + seconds

        gw = {
            "guild_id": ctx.guild.id,
            "channel_id": ctx.channel.id,
            "message_id": None,
            "host_id": ctx.author.id,
            "reward": reward,
            "winners_count": winners,
            "emoji_str": self.DEFAULT_REACTION_STR,
            "emoji_id": self.DEFAULT_REACTION_ID,
            "start_ts": int(time.time()),
            "end_ts": end_ts,
            "ended": False,
            "winner_ids": [],
            "claim_deadlines": {},
            "claim_notified": {},
        }

        embed = self._render_custom_embed(ctx.author.id, end_ts, winners, gw["reward"])

        try:
            msg = await ctx.send(embed=embed)
        except discord.Forbidden:
            return await ctx.send("❌ I do not have permission to send embeds here.")
        except Exception as e:
            return await ctx.send(f"❌ Failed to post giveaway: {e}")

        # Add reaction for entries (default emoji only)
        try:
            await msg.add_reaction(discord.PartialEmoji.from_str(self.DEFAULT_REACTION_STR))
        except discord.Forbidden:
            await ctx.send("⚠️ I need **Add Reactions** permission to allow entries.")
        except discord.HTTPException:
            await ctx.send("⚠️ I couldn't add the default emoji. Ensure the emoji exists and the bot can use it.")
        except Exception as e:
            await ctx.send(f"⚠️ Could not add entry reaction: {e}")

        # Save
        gw["message_id"] = msg.id
        self._ensure_guild(ctx.guild.id)["giveaways"][str(msg.id)] = gw
        self._set_latest_for_channel(ctx.guild.id, ctx.channel.id, msg.id)
        await self._save()

        # Re-edit (ensures consistency if needed)
        try:
            await msg.edit(embed=self._render_custom_embed(ctx.author.id, end_ts, winners, gw["reward"]))
        except Exception:
            pass

        await self._schedule_end_task(msg.id, gw)

    @commands.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def reroll(self, ctx: commands.Context, message_id: Optional[int] = None):
        if message_id is None:
            message_id = self._get_latest_for_channel(ctx.guild.id, ctx.channel.id)
            if message_id is None:
                return await ctx.send("❌ No recent giveaways found in this channel.")

        gw = self._get_giveaway(ctx.guild.id, message_id)
        if not gw:
            return await ctx.send("❌ I don't recognize that giveaway ID in this server.")
        if gw["channel_id"] != ctx.channel.id:
            return await ctx.send("❌ That giveaway is not in this channel.")

        try:
            entrants = await self._fetch_entrants(gw["channel_id"], message_id, gw["emoji_str"], gw["emoji_id"])
        except Exception as e:
            return await ctx.send(f"❌ Failed to fetch entrants for reroll: {e}")

        already = set(gw.get("winner_ids", []))
        pool = [u for u in entrants if u not in already and u != self.bot.user.id]
        if not pool:
            return await ctx.send("⚠️ No eligible entrants to reroll.")

        new_winner = random.choice(pool)
        gw["winner_ids"].append(new_winner)

        await ctx.send(f"<@{new_winner}> has won **{gw['reward']}**!")

        guild: discord.Guild = ctx.guild
        try:
            member = guild.get_member(new_winner) or await guild.fetch_member(new_winner)
        except Exception:
            member = None

        claim_seconds = self._claim_seconds_for_member(guild, member) if member else None
        if claim_seconds is not None:
            claim_until = int(time.time()) + max(1, claim_seconds)
            gw["claim_deadlines"][str(new_winner)] = claim_until
            gw.setdefault("claim_notified", {}).pop(str(new_winner), None)
            await ctx.send(f"You have until {discord_ts(claim_until, 'R')} to dm <@{gw['host_id']}>.")
            await self._schedule_claim_task(message_id, new_winner, claim_until)

        await self._save()

    # ----- In-channel ephemeral editor (no DMs) -----

    def _editor_embed(self, gw: Dict[str, Any]) -> discord.Embed:
        emb = discord.Embed(
            title="Giveaway Editor",
            description=(
                f"**Reward:** {discord.utils.escape_markdown(gw['reward'])}\n"
                f"**Winners:** {gw['winners_count']}\n"
                f"**Ends:** {discord_ts(gw['end_ts'], 'R')} ({discord_ts(gw['end_ts'], 'F')})\n"
                f"**Giveaway Message:** https://discord.com/channels/{gw['guild_id']}/{gw['channel_id']}/{gw['message_id']}"
            ),
            color=discord.Color.blurple(),
        ).set_footer(text="Changes update the original giveaway message.")
        return emb

    class _EditModal(discord.ui.Modal, title="Edit Giveaway"):
        def __init__(self, parent: "Giveaways", gw: Dict[str, Any]):
            super().__init__(timeout=180)
            self.parent = parent
            self.gw = gw

            self.duration = discord.ui.TextInput(
                label="New duration (from now), e.g. 30m, 2h (blank=keep)",
                style=discord.TextStyle.short,
                required=False,
                max_length=32,
            )
            self.winners = discord.ui.TextInput(
                label="New winner count (blank=keep)",
                style=discord.TextStyle.short,
                required=False,
                max_length=6,
            )
            self.reward = discord.ui.TextInput(
                label="New reward (blank=keep)",
                style=discord.TextStyle.short,
                required=False,
                max_length=100,
            )
            self.add_item(self.duration)
            self.add_item(self.winners)
            self.add_item(self.reward)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            changed = []
            s = str(self.duration.value).strip()
            if s:
                try:
                    secs = parse_duration_to_seconds(s)
                    self.gw["end_ts"] = int(time.time()) + secs
                    changed.append("duration → **updated**")
                except ValueError as e:
                    return await interaction.response.send_message(f"❌ Invalid duration: {e}", ephemeral=True)

            w = str(self.winners.value).strip()
            if w:
                if not w.isdigit() or int(w) <= 0:
                    return await interaction.response.send_message("❌ Winners must be a positive integer.", ephemeral=True)
                self.gw["winners_count"] = int(w)
                changed.append(f"winners → **{w}**")

            r = str(self.reward.value).strip()
            if r:
                self.gw["reward"] = r
                changed.append("reward → **updated**")

            try:
                guild = interaction.client.get_guild(self.gw["guild_id"]) or await interaction.client.fetch_guild(self.gw["guild_id"])
                await self.parent._update_gw_message(guild, self.gw)
            except Exception as e:
                await interaction.response.send_message(f"⚠️ Updated data, but failed to edit message: {e}", ephemeral=True)
                await self.parent._save()
                return

            await self.parent._schedule_end_task(self.gw["message_id"], self.gw)
            await self.parent._save()
            summary = ", ".join(changed) if changed else "no fields changed."
            await interaction.response.send_message(f"✅ Updated giveaway: {summary}", ephemeral=True)

    class _EditView(discord.ui.View):
        def __init__(self, parent: "Giveaways", author_id: int, gw: Dict[str, Any]):
            super().__init__(timeout=300)
            self.parent = parent
            self.author_id = author_id
            self.gw = gw

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.author_id:
                return True
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member and member.guild_permissions.manage_guild:
                return True
            await interaction.response.send_message("❌ You are not allowed to use this editor.", ephemeral=True)
            return False

        @discord.ui.button(label="Edit Fields", style=discord.ButtonStyle.primary)
        async def edit_fields(self, interaction: discord.Interaction, _: discord.ui.Button):
            await interaction.response.send_modal(Giveaways._EditModal(self.parent, self.gw))

        @discord.ui.button(label="End Now", style=discord.ButtonStyle.danger)
        async def end_now(self, interaction: discord.Interaction, _: discord.ui.Button):
            await interaction.response.defer(ephemeral=True, thinking=False)
            if self.gw.get("ended"):
                return await interaction.followup.send("This giveaway has already ended.", ephemeral=True)
            await self.parent._end_giveaway(self.gw)
            await interaction.followup.send("☑️ Giveaway ended.", ephemeral=True)

        @discord.ui.button(label="Close Panel", style=discord.ButtonStyle.secondary)
        async def close_panel(self, interaction: discord.Interaction, _: discord.ui.Button):
            await interaction.response.defer(ephemeral=True, thinking=False)
            self.stop()
            await interaction.followup.send("Editor closed.", ephemeral=True)

    class _OpenEditorView(discord.ui.View):
        def __init__(self, parent: "Giveaways", author_id: int, gw: Dict[str, Any]):
            super().__init__(timeout=180)
            self.parent = parent
            self.author_id = author_id
            self.gw = gw

        @discord.ui.button(label="Open Editor (ephemeral)", style=discord.ButtonStyle.primary)
        async def open_editor(self, interaction: discord.Interaction, _: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("This editor is for the command invoker only.", ephemeral=True)
            await interaction.response.send_message(embed=self.parent._editor_embed(self.gw),
                                                    view=Giveaways._EditView(self.parent, self.author_id, self.gw),
                                                    ephemeral=True)

    @commands.hybrid_command(name="gwedit", with_app_command=True, description="Edit a giveaway (ephemeral control panel).")
    @commands.has_permissions(manage_guild=True)
    async def edit_giveaway(self, ctx: commands.Context, message_id: int):
        gw = self._get_giveaway(ctx.guild.id, message_id)
        if not gw:
            return await ctx.send("❌ I don't recognize that giveaway ID in this server.")
        if gw["channel_id"] != ctx.channel.id:
            return await ctx.send("❌ That giveaway is not in this channel.")

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=self._editor_embed(gw),
                                                        view=self._EditView(self, ctx.author.id, gw),
                                                        ephemeral=True)
        else:
            note = "(Only you will see the editor after clicking the button below.)"
            await ctx.send(f"Opening an ephemeral editor for you {ctx.author.mention} {note}",
                           view=self._OpenEditorView(self, ctx.author.id, gw))

# -------- Cog setup --------

async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
