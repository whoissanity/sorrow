# bot/cogs/crypto.py
import io
from datetime import datetime, timezone
from typing import Optional, Tuple

import aiohttp
import discord
from discord.ext import commands

# Matplotlib setup (nicer styling)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

COINGECKO = "https://api.coingecko.com/api/v3"

async def _cg_search_coin(session: aiohttp.ClientSession, query: str) -> Optional[dict]:
    async with session.get(f"{COINGECKO}/search", params={"query": query}, timeout=10) as r:
        if r.status != 200:
            return None
        data = await r.json()
    q = query.lower()
    # Try exact symbol, then exact name, then first match
    for c in data.get("coins", []):
        if c.get("symbol", "").lower() == q:
            return c
    for c in data.get("coins", []):
        if c.get("name", "").lower() == q:
            return c
    return (data.get("coins") or [None])[0]

async def _cg_simple_price_and_change(session: aiohttp.ClientSession, coin_id: str) -> Optional[Tuple[float, Optional[float]]]:
    """
    Returns (price_usd, change_pct_24h). change may be None if API doesn't return it.
    """
    params = {
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    async with session.get(f"{COINGECKO}/simple/price", params=params, timeout=10) as r:
        if r.status != 200:
            return None
        data = await r.json()
    try:
        info = data[coin_id]
        price = float(info["usd"])
        change = info.get("usd_24h_change")
        change = float(change) if change is not None else None
        return price, change
    except Exception:
        return None

async def _cg_market_chart(session: aiohttp.ClientSession, coin_id: str, days: int = 7):
    async with session.get(
        f"{COINGECKO}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": days, "interval": "hourly"},
        timeout=10,
    ) as r:
        if r.status != 200:
            return None
        data = await r.json()
    return data.get("prices", [])

def _plot_prices(prices: list, title: str) -> io.BytesIO:
    # prices: [[ms, price], ...]
    xs = [datetime.fromtimestamp(p[0] / 1000.0, tz=timezone.utc) for p in prices]
    ys = [float(p[1]) for p in prices]

    # Simple smoothing: 10-pt moving average (visual only)
    if len(ys) >= 10:
        sm = []
        win = 10
        csum = [0.0]
        for v in ys:
            csum.append(csum[-1] + v)
        for i in range(len(ys)):
            j = max(0, i - win + 1)
            sm.append((csum[i + 1] - csum[j]) / (i - j + 1))
    else:
        sm = ys

    fig = plt.figure(figsize=(7.5, 3.5), dpi=170)
    ax = fig.add_subplot(111)

    ax.plot(xs, sm, linewidth=2.0)
    ax.fill_between(xs, sm, alpha=0.15)
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.set_title(title, pad=12)
    ax.set_ylabel("USD")
    ax.set_xlabel("Time (UTC)")

    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

class Crypto(commands.Cog, name="Crypto"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="crypto", usage="<coin>")
    async def crypto_price(self, ctx: commands.Context, *, coin: str):
        """Show current price, 24h change in brackets, and a pretty 7d chart."""
        async with aiohttp.ClientSession() as s:
            found = await _cg_search_coin(s, coin)
            if not found:
                return await ctx.send("coin not found.")
            coin_id = found["id"]
            name = found.get("name") or coin_id

            pc = await _cg_simple_price_and_change(s, coin_id)
            if pc is None:
                return await ctx.send("failed to fetch price.")
            price, change = pc

            chart = await _cg_market_chart(s, coin_id, days=7)

        # Build the price line with (+/-)xx.xx%
        if change is None:
            header = f"{name} — ${price:,.2f}"
        else:
            header = f"{name} — ${price:,.2f} ({change:+.2f}%)"

        if not chart:
            return await ctx.send(header)

        img = _plot_prices(chart, f"{name} — 7d")
        await ctx.send(header, file=discord.File(img, filename=f"{coin_id}_7d.png"))

    @commands.command(name="bal", usage="<coin> <address>")
    async def crypto_balance(self, ctx: commands.Context, coin: str, address: str):
        """Quick wallet balance. Supports: btc, eth"""
        c = coin.lower()
        async with aiohttp.ClientSession() as s:
            if c in ("btc", "bitcoin"):
                # Blockstream API (satoshis)
                url = f"https://blockstream.info/api/address/{address}"
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return await ctx.send("failed to fetch balance.")
                    data = await r.json()
                funded = int(data.get("chain_stats", {}).get("funded_txo_sum", 0))
                spent = int(data.get("chain_stats", {}).get("spent_txo_sum", 0))
                sats = max(0, funded - spent)
                btc = sats / 1e8
                return await ctx.send(f"BTC balance: {btc:.8f} BTC")
            if c in ("eth", "ethereum"):
                # Ethplorer free key
                url = f"https://api.ethplorer.io/getAddressInfo/{address}?apiKey=freekey"
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return await ctx.send("failed to fetch balance.")
                    data = await r.json()
                eth = float(data.get("ETH", {}).get("balance", 0.0))
                return await ctx.send(f"ETH balance: {eth:.6f} ETH")
            if c in ("usdt", "tether"):
                url = f""
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return await ctx.send("failed to fetch balance.")
                    data = await r.json()
        await ctx.send("unsupported coin. try btc or eth.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Crypto(bot))
