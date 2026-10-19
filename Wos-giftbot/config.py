"""Configuration for the WOS gift-code bot. Everything comes from environment variables."""
import os

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

# Discord channel ID where the existing code-posting bot posts codes.
WATCH_CHANNEL_ID = int(os.environ.get("WATCH_CHANNEL_ID", "0") or 0)

# Optional: Discord user ID of the existing bot that posts codes.
# If set, only its messages are scanned. If 0, every message in the
# watch channel is scanned.
POSTER_BOT_ID = int(os.environ.get("POSTER_BOT_ID", "0") or 0)

# Discord user IDs allowed to run admin commands (/redeem, /stats).
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# Regex used to pull gift codes out of watched messages.
CODE_PATTERN = os.environ.get("CODE_PATTERN", r"(?<![A-Za-z0-9])[A-Za-z0-9]{6,24}(?![A-Za-z0-9])")

# How many redemptions to run concurrently per code.
REDEEM_CONCURRENCY = int(os.environ.get("REDEEM_CONCURRENCY", "5") or 5)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "giftbot.db"))
