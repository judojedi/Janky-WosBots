"""Discord bot: watches for gift codes, auto-redeems them for registered players."""
import asyncio
import logging
import re

import aiohttp
import discord
from discord import app_commands

import config
import db
import wos_api

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wos-giftbot")

CODE_RE = re.compile(r"Codes?:\s*([a-zA-Z0-9]+)", re.IGNORECASE)


def _clean(text: str) -> str:
    return (text or "").replace("*", "").replace("`", "").replace("_", "")


def extract_codes_from_message(message: discord.Message):
    texts = [_clean(message.content)]
    for embed in message.embeds:
        texts.append(_clean(embed.title))
        texts.append(_clean(embed.description))
        for field in embed.fields:
            texts.append(_clean(field.name))
            texts.append(_clean(field.value))
            # poster bots often split label and code across field name/value
            # ("Code" / "`26FullMoon`"); search them joined too
            texts.append(f"{_clean(field.name)}: {_clean(field.value)}")
    found = []
    for text in texts:
        for m in CODE_RE.finditer(text):
            token = m.group(1)
            if token not in found:
                found.append(token)
    return found


class GiftBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._redeem_lock = asyncio.Lock()

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)

    async def on_message(self, message: discord.Message):
        if message.author == self.user or message.guild is None:
            return
        if config.WATCH_CHANNEL_ID and message.channel.id != config.WATCH_CHANNEL_ID:
            return
        if config.POSTER_BOT_ID and message.author.id != config.POSTER_BOT_ID:
            return
        codes = [c for c in extract_codes_from_message(message) if db.mark_seen(c)]
        for code in codes:
            await self.redeem_for_all(message.channel, code, source="auto")

    async def redeem_for_all(self, channel, code: str, source: str):
        users = [(d, f, k) for d, f, k in db.all_users() if k]
        missing_kid = db.user_count() - len(users)
        if not users:
            msg = f"No players with a State set — nothing to redeem `{code}` for."
            if missing_kid:
                msg += f" ({missing_kid} registered without a state; ask them to re-run /register with their state number.)"
            await channel.send(msg)
            return
        async with self._redeem_lock:
            await channel.send(
                f"🎁 New code detected (`{source}`): `{code}` — redeeming for {len(users)} registered player(s)…"
            )
            sem = asyncio.Semaphore(config.REDEEM_CONCURRENCY)

            async def one(session, discord_id, fid, kid):
                async with sem:
                    status = wos_api.UNKNOWN
                    for attempt in range(3):
                        try:
                            status = await wos_api.redeem_code(session, fid, kid, code)
                        except wos_api.RedeemError as e:
                            log.warning("redeem %s for %s failed (attempt %d): %s", code, fid, attempt + 1, e)
                            status = wos_api.TIMEOUT_RETRY
                        if status != wos_api.TIMEOUT_RETRY:
                            break
                        await asyncio.sleep(2 ** attempt)
                    db.record_redemption(fid, code, status, "")
                    return status

            async with aiohttp.ClientSession() as session:
                results = await asyncio.gather(*[one(session, d, f, k) for d, f, k in users])

            ok = sum(1 for s in results if s in (wos_api.SUCCESS, wos_api.ALREADY_CLAIMED))
            detail = ""
            problems = {}
            for s in results:
                if s not in (wos_api.SUCCESS, wos_api.ALREADY_CLAIMED):
                    problems[s] = problems.get(s, 0) + 1
            if problems:
                detail = " Issues: " + ", ".join(f"{v}× {k}" for k, v in problems.items()) + "."
            await channel.send(f"Done with `{code}`: ✅ {ok}/{len(users)} got it.{detail}")


client = GiftBot()


def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in config.ADMIN_IDS


@client.tree.command(name="register", description="Register your Whiteout Survival account for auto-redemption")
@app_commands.describe(
    fid="Your numeric Player ID (tap your avatar, top-left in game)",
    kid="Your State number (shown under your name, e.g. 123)",
)
async def register_cmd(interaction: discord.Interaction, fid: str, kid: str):
    fid, kid = fid.strip(), kid.strip()
    if not fid.isdigit() or not kid.isdigit():
        await interaction.response.send_message(
            "Both Player ID and State should be numbers.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        verdict = await wos_api.probe_kid(session, fid, kid)
    if verdict == "nomatch":
        await interaction.followup.send(
            f"⚠️ Player ID `{fid}` doesn't seem to be in State `{kid}` — double-check the numbers and try again. "
            "Your State number is shown under your name on the main game screen.",
            ephemeral=True,
        )
        return
    db.register(interaction.user.id, fid, kid)
    note = "" if verdict == "match" else " (couldn't verify the state just now — if redemptions fail, re-check it)"
    await interaction.followup.send(
        f"✅ Registered! Codes will be auto-redeemed for Player ID `{fid}` in State `{kid}`.{note}",
        ephemeral=True,
    )


@client.tree.command(name="unregister", description="Stop auto-redeeming gift codes for your account")
async def unregister_cmd(interaction: discord.Interaction):
    if db.unregister(interaction.user.id):
        await interaction.response.send_message("❌ Unregistered — I won't redeem codes for you anymore.", ephemeral=True)
    else:
        await interaction.response.send_message("You weren't registered.", ephemeral=True)


@client.tree.command(name="status", description="Show your registration status")
async def status_cmd(interaction: discord.Interaction):
    fid, kid = db.get_user(interaction.user.id)
    if fid:
        await interaction.response.send_message(
            f"You're registered — Player ID `{fid}`, State `{kid or 'not set'}`.", ephemeral=True
        )
    else:
        await interaction.response.send_message("You're not registered. Use /register to sign up.", ephemeral=True)


@client.tree.command(name="redeem", description="[Admin] Manually redeem a code for everyone")
@app_commands.describe(code="The gift code to redeem")
async def redeem_cmd(interaction: discord.Interaction, code: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    code = code.strip()
    if not db.mark_seen(code):
        await interaction.response.send_message(f"`{code}` was already processed.", ephemeral=True)
        return
    await interaction.response.send_message(f"On it — redeeming `{code}` for everyone…")
    await client.redeem_for_all(interaction.channel, code, source="manual")


@client.tree.command(name="stats", description="[Admin] Show bot stats")
async def stats_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"📊 {db.user_count()} registered player(s).", ephemeral=True
    )


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first.")
    db.init()
    client.run(config.DISCORD_TOKEN)
