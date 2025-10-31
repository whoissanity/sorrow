import discord
from typing import Optional

def success(title: str, description: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=discord.Color.green())
    return e

def error(title: str, description: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=discord.Color.red())
    return e

def info(title: str, description: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    return e
