import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from mcipc.rcon.je import Client
from mcipc.query import Client as QueryClient
import asyncio
import aiohttp
import logging
import os
import json
from typing import Optional

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_IP = os.getenv("SERVER_IP")
RCON_PORT = os.getenv("RCON_PORT")
RCON_PASSWORD = os.getenv("RCON_PASSWORD")
QUERY_PORT = os.getenv("QUERY_PORT")
CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # z.B. "owner/repo"
GITHUB_UPDATES_CHANNEL_ID = os.getenv("GITHUB_UPDATES_CHANNEL_ID")
GITHUB_POLL_INTERVAL_SECONDS = os.getenv("GITHUB_POLL_INTERVAL_SECONDS", "120")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("betterMCbot")

if not TOKEN:
    raise SystemExit("Fehlende Environment Variable: DISCORD_TOKEN")

def _parse_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None

RCON_PORT_INT = _parse_int(RCON_PORT)
QUERY_PORT_INT = _parse_int(QUERY_PORT)
CHAT_CHANNEL_ID_INT = _parse_int(CHAT_CHANNEL_ID)
GITHUB_UPDATES_CHANNEL_ID_INT = _parse_int(GITHUB_UPDATES_CHANNEL_ID)
GITHUB_POLL_INTERVAL = _parse_int(GITHUB_POLL_INTERVAL_SECONDS) or 120

HAS_RCON = bool(SERVER_IP and RCON_PASSWORD and RCON_PORT_INT)
HAS_QUERY = bool(SERVER_IP and QUERY_PORT_INT)
HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)
HAS_GITHUB = bool(GITHUB_REPO and GITHUB_UPDATES_CHANNEL_ID_INT)

_last_seen_commit_sha = None

def _load_config_from_file():
    try:
        if not os.path.exists(CONFIG_PATH):
            return {}
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Konfigurationsdatei konnte nicht geladen werden: %s", exc)
        return {}

def _save_config_to_file(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Konfigurationsdatei konnte nicht gespeichert werden: %s", exc)

def _apply_runtime_config(data):
    global CHAT_CHANNEL_ID_INT, GITHUB_REPO, GITHUB_UPDATES_CHANNEL_ID_INT, GITHUB_POLL_INTERVAL
    global HAS_BRIDGE, HAS_GITHUB

    chat_id = _parse_int(data.get("chat_channel_id"))
    if chat_id is not None:
        CHAT_CHANNEL_ID_INT = chat_id

    repo = data.get("github_repo")
    if isinstance(repo, str) and repo.strip():
        GITHUB_REPO = repo.strip()

    gh_channel = _parse_int(data.get("github_updates_channel_id"))
    if gh_channel is not None:
        GITHUB_UPDATES_CHANNEL_ID_INT = gh_channel

    poll_int = _parse_int(str(data.get("github_poll_interval_seconds")))
    if poll_int:
        GITHUB_POLL_INTERVAL = poll_int

    HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)
    HAS_GITHUB = bool(GITHUB_REPO and GITHUB_UPDATES_CHANNEL_ID_INT)

_apply_runtime_config(_load_config_from_file())

async def fetch_latest_commits(session, repo_full_name):
    url = f"https://api.github.com/repos/{repo_full_name}/commits"
    headers = {"Accept": "application/vnd.github+json"}
    async with session.get(url, headers=headers, timeout=20) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"GitHub API {resp.status}: {text}")
        return await resp.json()

async def github_updates_task():
    global _last_seen_commit_sha
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            try:
                if not HAS_GITHUB:
                    await asyncio.sleep(GITHUB_POLL_INTERVAL)
                    continue
                channel = bot.get_channel(GITHUB_UPDATES_CHANNEL_ID_INT)
                if channel is None:
                    logger.warning("GitHub-Updates-Channel nicht gefunden: %s", GITHUB_UPDATES_CHANNEL_ID_INT)
                    await asyncio.sleep(GITHUB_POLL_INTERVAL)
                    continue
                commits = await fetch_latest_commits(session, GITHUB_REPO)
                if not isinstance(commits, list) or not commits:
                    await asyncio.sleep(GITHUB_POLL_INTERVAL)
                    continue
                newest = commits[0]
                sha = newest.get("sha")
                if _last_seen_commit_sha is None:
                    _last_seen_commit_sha = sha
                elif sha != _last_seen_commit_sha:
                    # Finde neue Commits bis zum letzten gesehenen
                    new_items = []
                    for item in commits:
                        if item.get("sha") == _last_seen_commit_sha:
                            break
                        new_items.append(item)
                    # In chronologischer Reihenfolge posten (alt -> neu)
                    for item in reversed(new_items):
                        commit = item.get("commit", {})
                        author = commit.get("author", {}).get("name", "?")
                        message = commit.get("message", "")
                        url = item.get("html_url", "")
                        await channel.send(f"[GitHub] {author}: {message}\n{url}")
                    _last_seen_commit_sha = sha
            except Exception as exc:
                logger.warning("GitHub Updates Fehler: %s", exc)
            await asyncio.sleep(GITHUB_POLL_INTERVAL)

discord_command_prefix = "-"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(description="Discord Chatbot", command_prefix=discord_command_prefix, intents=intents)


@bot.event
async def on_ready():
    logger.info("Bot Ready als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    logger.info("Verbunden mit %d Guild(s)", len(bot.guilds))
    logger.info(
        "Features: bridge=%s, rcon=%s, query=%s, github=%s",
        "on" if HAS_BRIDGE else "off",
        "on" if HAS_RCON else "off",
        "on" if HAS_QUERY else "off",
        "on" if HAS_GITHUB else "off",
    )
    if HAS_GITHUB:
        bot.loop.create_task(github_updates_task())
    try:
        await bot.tree.sync()
        logger.info("Slash-Commands synchronisiert")
    except Exception as exc:
        logger.warning("Slash-Commands Sync fehlgeschlagen: %s", exc)


@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if (await bot.get_context(message)).command is not None:
        return
    if message.author == bot.user or message.author.bot:
        return
    if not HAS_BRIDGE:
        return
    if message.channel.id != CHAT_CHANNEL_ID_INT:
        return
    try:
        with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
            client.say("[Discord] " + message.author.name + ": " + message.content)
    except Exception as exc:
        logger.warning("RCON Send fehlgeschlagen: %s", exc)


@bot.command(name='whitelist')
async def whitelist(ctx, *, arg):
    name = arg
    try:
        if not HAS_RCON:
            await ctx.send("Minecraft-RCON ist nicht konfiguriert.")
            return
        with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
            whitelist = client.whitelist
            whitelist.add(name)
            await ctx.send("Spieler " + name + " wurde zur Whitelist hinzugefügt")
    except Exception as e:
        await ctx.send("Server nicht erreichbar")


@bot.command(name='ping')
async def ping(ctx):
    try:
        if not HAS_QUERY:
            await ctx.send("Minecraft-Query ist nicht konfiguriert.")
            return
        with QueryClient(SERVER_IP, QUERY_PORT_INT) as client:
            status = client.stats(full=True)
            ans = "Server ist online mit " + str(status['num_players']) + "/" + str(
                status['max_players']) + " Spielern:"
            for player in status['players']:
                ans += "\n\t" + player
            await ctx.send(ans)
    except Exception as e:
        await ctx.send("Server ist offline")


#        if status['online'] == 0:
#            await ctx.send("Server is offline")
#        else:
#            res =


bot.run(TOKEN)

# ------------------------------
# Slash Commands (Konfiguration)
# ------------------------------

@bot.tree.command(name="set_server_channel", description="Setzt den Discord-Channel für die Minecraft-Brücke")
@app_commands.describe(channel="Ziel-Channel für Brücke")
@app_commands.default_permissions(manage_guild=True)
async def set_server_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data = _load_config_from_file()
    data["chat_channel_id"] = channel.id
    _save_config_to_file(data)
    _apply_runtime_config(data)
    await interaction.response.send_message(f"Brücken-Channel gesetzt auf {channel.mention}.", ephemeral=True)


@bot.tree.command(name="set_githubupdate_channel", description="Konfiguriert Repo und Channel für GitHub-Commit-Updates")
@app_commands.describe(repo="owner/repo", channel="Ziel-Channel", poll_interval_seconds="optional, Standard 120s")
@app_commands.default_permissions(manage_guild=True)
async def set_githubupdate_channel(interaction: discord.Interaction, repo: str, channel: discord.TextChannel, poll_interval_seconds: Optional[int] = None):
    global _last_seen_commit_sha
    repo = repo.strip()
    if "/" not in repo:
        await interaction.response.send_message("Ungültiges Repo-Format. Erwartet: owner/repo", ephemeral=True)
        return
    data = _load_config_from_file()
    data["github_repo"] = repo
    data["github_updates_channel_id"] = channel.id
    if poll_interval_seconds and poll_interval_seconds > 0:
        data["github_poll_interval_seconds"] = poll_interval_seconds
    _save_config_to_file(data)
    _apply_runtime_config(data)
    _last_seen_commit_sha = None
    await interaction.response.send_message(f"GitHub-Updates gesetzt: {repo} → {channel.mention}.", ephemeral=True)


@bot.tree.command(name="disable_github", description="Deaktiviert GitHub-Commit-Updates")
@app_commands.default_permissions(manage_guild=True)
async def disable_github(interaction: discord.Interaction):
    data = _load_config_from_file()
    data.pop("github_repo", None)
    data.pop("github_updates_channel_id", None)
    _save_config_to_file(data)
    _apply_runtime_config(data)
    await interaction.response.send_message("GitHub-Updates deaktiviert.", ephemeral=True)


@bot.tree.command(name="show_config", description="Zeigt die aktuelle Bot-Konfiguration")
@app_commands.default_permissions(manage_guild=True)
async def show_config(interaction: discord.Interaction):
    data = {
        "bridge_channel_id": CHAT_CHANNEL_ID_INT,
        "github_repo": GITHUB_REPO,
        "github_updates_channel_id": GITHUB_UPDATES_CHANNEL_ID_INT,
        "github_poll_interval_seconds": GITHUB_POLL_INTERVAL,
        "features": {
            "bridge": HAS_BRIDGE,
            "rcon": HAS_RCON,
            "query": HAS_QUERY,
            "github": HAS_GITHUB,
        },
    }
    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    await interaction.response.send_message(f"```json\n{pretty}\n```", ephemeral=True)
