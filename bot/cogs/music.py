from __future__ import annotations
import asyncio

from typing import Optional, Dict, List

import discord
from discord.ext import commands
import yt_dlp
import imageio_ffmpeg

# --- yt-dlp options (info only; never download files) ---
YTDL_INFO = {
    "quiet": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "skip_download": True,
    "extract_flat": False,
}
YTDL_FLAT = {
    "quiet": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "skip_download": True,
    "extract_flat": True,
}

# Prefer system ffmpeg if present; fallback to imageio one
def _ffmpeg_path() -> str:
    try:
        import shutil
        p = shutil.which("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    return imageio_ffmpeg.get_ffmpeg_exe()

FFMPEG_BIN = _ffmpeg_path()

# Lower startup latency & reduce reconnect hiccups
FFMPEG_BEFORE = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -fflags +nobuffer -analyzeduration 0 -probesize 32k"
FFMPEG_OPTS = "-vn"

def is_spotify(url: str) -> bool:
    u = url.lower()
    return "open.spotify.com/" in u or u.startswith("spotify:")

def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "spotify:"))

# ---------- yt-dlp helpers ----------
async def _extract(url: str, flat: bool = False) -> Optional[dict]:
    opts = YTDL_FLAT if flat else YTDL_INFO
    def _do():
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False)
    try:
        return await asyncio.to_thread(_do)
    except Exception:
        return None

async def _yt_search_one(query: str) -> Optional[dict]:
    info = await _extract(f"ytsearch1:{query}", flat=False)
    if not info:
        return None
    if "entries" in info and info["entries"]:
        return info["entries"][0]
    return info

async def _best_stream_info(url_or_query: str) -> Optional[dict]:
    """
    Return streamable info for low‑latency playback.
    Prefer opus/webm audio to avoid heavy transcoding.
    """
    def _do(u: str):
        yopt = {
            "quiet": True,
            "nocheckcertificate": True,
            "ignoreerrors": True,
            # Prefer opus @ 48kHz (native for Discord) then fallback to bestaudio
            "format": "bestaudio[acodec=opus][asr=48000]/bestaudio/best",
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(yopt) as y:
            return y.extract_info(u, download=False)
    try:
        info = await asyncio.to_thread(_do, url_or_query)
    except Exception:
        return None
    if not info:
        return None
    if "url" in info:
        return info
    if "entries" in info and info["entries"]:
        e = info["entries"][0]
        if e and "url" in e:
            return e
        if e and "webpage_url" in e:
            return await _best_stream_info(e["webpage_url"])
    return None

async def _spotify_to_youtube(url: str) -> List[dict]:
    out: List[dict] = []
    meta = await _extract(url, flat=True)
    if not meta: return out
    for e in (meta.get("entries") or []):
        if not e: continue
        title = e.get("title") or e.get("track") or ""
        artist = e.get("artist") or (", ".join(e["artists"]) if isinstance(e.get("artists"), list) else e.get("artists") or "")
        q = f"{title} {artist}".strip() or title
        if not q: continue
        first = await _yt_search_one(q)
        if first: out.append(first)
    return out

async def resolve_to_queue_items(inp: str) -> List[dict]:
    """
    Normalize input into items: {"title": str, "query": str}
    - YouTube playlist: expands entries
    - Spotify link/playlist: mapped to YouTube top matches
    - Plain text: YouTube search
    """
    items: List[dict] = []
    s = inp.strip()

    if is_url(s):
        if is_spotify(s):
            ents = await _spotify_to_youtube(s)
            for e in ents:
                title = e.get("title") or "track"
                url = e.get("webpage_url") or e.get("url") or s
                items.append({"title": title, "query": url})
            return items
        info = await _extract(s, flat=True)
        if not info:
            return items
        if "entries" in info and info["entries"]:
            for e in info["entries"]:
                if not e: continue
                title = e.get("title") or "track"
                url = e.get("webpage_url") or e.get("url") or s
                items.append({"title": title, "query": url})
            return items
        title = info.get("title") or s
        url = info.get("webpage_url") or s
        items.append({"title": title, "query": url})
        return items

    first = await _yt_search_one(s)
    if first:
        title = first.get("title") or s
        url = first.get("webpage_url") or first.get("url") or s
        items.append({"title": title, "query": url})
    return items

# ---------- Player ----------
class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: List[Dict] = []
        self.playing: bool = False
        self.lock = asyncio.Lock()
        self.prefetch_task: Optional[asyncio.Task] = None

    async def ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not isinstance(ctx.author, discord.Member) or not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("join a voice channel first.")
            return None
        vc = ctx.voice_client
        if vc and vc.channel == ctx.author.voice.channel:
            return vc
        if vc and vc.is_connected():
            try:
                await vc.move_to(ctx.author.voice.channel)
                return vc
            except Exception:
                try: await vc.disconnect(force=True)
                except Exception: pass
        try:
            return await ctx.author.voice.channel.connect()
        except Exception:
            await ctx.send("failed to connect to voice.")
            return None

    async def enqueue(self, items: List[Dict]):
        self.queue.extend(items)
        # prefetch the very next item's stream_url in the background
        await self._kick_prefetch()

    async def _kick_prefetch(self):
        # avoid multiple running prefetch tasks
        if self.prefetch_task and not self.prefetch_task.done():
            return
        if not self.queue:
            return
        async def _prefetch():
            try:
                item = self.queue[0]
                if not item.get("stream_url"):
                    info = await _best_stream_info(item["query"])
                    if info and "url" in info:
                        item["stream_url"] = info["url"]
            except Exception:
                pass
        self.prefetch_task = self.bot.loop.create_task(_prefetch())

    async def _after_track(self, error: Optional[Exception]):
        self.playing = False
        vc = discord.utils.get(self.bot.voice_clients, guild__id=self.guild_id)  # type: ignore
        if vc and vc.is_connected():
            await self._play_next(vc)

    async def _play_next(self, vc: discord.VoiceClient):
        async with self.lock:
            if self.playing:
                return
            if not self.queue:
                return
            item = self.queue.pop(0)
            url = item.get("stream_url")
            if not url:
                info = await _best_stream_info(item["query"])
                if not info or "url" not in info:
                    # try next item
                    if self.queue:
                        return await self._play_next(vc)
                    return
                url = info["url"]
            # start prefetch for the following item to reduce gap
            await self._kick_prefetch()

            # Use Opus output for stability/CPU efficiency
            source = discord.FFmpegOpusAudio(
                url,
                executable=FFMPEG_BIN,
                before_options=FFMPEG_BEFORE,
                options=FFMPEG_OPTS,
                bitrate=None  # let ffmpeg pick sensible bitrate
            )
            self.playing = True

            def _cb(err):
                self.bot.loop.call_soon_threadsafe(asyncio.create_task, self._after_track(err))

            vc.play(source, after=_cb)

# ---------- Cog ----------
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: Dict[int, GuildPlayer] = {}

    def _player(self, guild_id: int) -> GuildPlayer:
        p = self.players.get(guild_id)
        if not p:
            p = GuildPlayer(self.bot, guild_id)
            self.players[guild_id] = p
        return p

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        if not query:
            return await ctx.send('usage: ",play <YouTube/Spotify link or search>"')
        player = self._player(ctx.guild.id)
        vc = await player.ensure_voice(ctx)
        if not vc:
            return
        items = await resolve_to_queue_items(query)
        if not items:
            return await ctx.send("couldn't find anything to play.")
        await player.enqueue(items)

        # If we're idle, start immediately and report the first link we just started
        starting_now = (not vc.is_playing() and not player.playing)
        if starting_now:
            first_url = items[0]["query"]
            await player._play_next(vc)
            await ctx.send(f"playing {first_url}")
        else:
            # Already playing — still follow your format with the first enqueued link
            await ctx.send(f"playing {items[0]['query']}")

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc or not vc.is_playing():
            return
        try:
            vc.stop()
        except Exception:
            pass
        await ctx.send("ok.")

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc:
            return
        try:
            vc.stop()
            await vc.disconnect(force=True)
        except Exception:
            pass
        await ctx.send("ok.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
