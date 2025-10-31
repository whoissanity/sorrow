import os
from dataclasses import dataclass

@dataclass
class Settings:
    token: str
    owners: set[int]

def load_settings() -> Settings:
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    owners = {int(x) for x in os.getenv("BOT_OWNERS", "").replace(" ", "").split(",") if x}
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN in environment (.env)")
    return Settings(token=token, owners=owners)
