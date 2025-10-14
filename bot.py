import os

# Voice-Funktionen deaktivieren, um audioop-Import zu vermeiden (z. B. unter Python 3.13)
os.environ.setdefault("DISCORD_DISABLE_VOICE", "1")

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from mcipc.rcon.je import Client
from mcipc.query import Client as QueryClient
import asyncio
import aiohttp
from aiohttp import web
import logging
import json
from typing import Optional
import hmac
import hashlib
from supabase import create_client
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

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
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "bot_config")
MESSAGE_CLEANUP_RETENTION_HOURS = os.getenv("MESSAGE_CLEANUP_RETENTION_HOURS", "48")
MESSAGE_CLEANUP_INTERVAL_MINUTES = os.getenv("MESSAGE_CLEANUP_INTERVAL_MINUTES", "60")
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

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
WEBHOOK_ACTIVE = bool(GITHUB_WEBHOOK_SECRET)
MESSAGE_CLEANUP_RETENTION_HOURS_INT = _parse_int(MESSAGE_CLEANUP_RETENTION_HOURS) or 48
MESSAGE_CLEANUP_INTERVAL_MINUTES_INT = _parse_int(MESSAGE_CLEANUP_INTERVAL_MINUTES) or 60

# Countdown-Konfiguration
COUNTDOWN_CHANNEL_ID_INT = None
COUNTDOWN_TARGET_ISO = None  # ISO-String ohne/mit TZ; naive wird in COUNTDOWN_TZ interpretiert
COUNTDOWN_TZ = DEFAULT_TIMEZONE
COUNTDOWN_LAST_EVENT_ID = None

HAS_RCON = bool(SERVER_IP and RCON_PASSWORD and RCON_PORT_INT)
HAS_QUERY = bool(SERVER_IP and QUERY_PORT_INT)
HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)
HAS_GITHUB = bool(GITHUB_REPO and GITHUB_UPDATES_CHANNEL_ID_INT)

_last_seen_commit_sha = None
_supabase = None

def _init_supabase():
    global _supabase
    if _supabase is not None:
        return
    if not SUPABASE_URL:
        return
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not key:
        return
    try:
        _supabase = create_client(SUPABASE_URL, key)
        logger.info("Supabase-Client initialisiert")
    except Exception as exc:
        logger.warning("Supabase-Init fehlgeschlagen: %s", exc)

def _load_config_from_supabase():
    if _supabase is None:
        return None
    try:
        res = _supabase.table(SUPABASE_TABLE).select("config").eq("id", 1).limit(1).execute()
        rows = getattr(res, "data", []) or []
        if not rows:
            return {}
        cfg = rows[0].get("config")
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.warning("Supabase Load fehlgeschlagen: %s", exc)
        return None

def _save_config_to_supabase(data):
    if _supabase is None:
        return False
    try:
        _supabase.table(SUPABASE_TABLE).upsert({"id": 1, "config": data}).execute()
        return True
    except Exception as exc:
        logger.warning("Supabase Save fehlgeschlagen: %s", exc)
        return False

def load_config():
    _init_supabase()
    data = _load_config_from_supabase()
    if data is None:
        data = _load_config_from_file()
    return data or {}

def save_config(data):
    saved = _save_config_to_supabase(data)
    if not saved:
        _save_config_to_file(data)

# Dynamisches Prefix (per Slash-Command änderbar)
COMMAND_PREFIX = "mc!"

def get_command_prefix(_bot, message):
    # Global dynamisches Prefix (z. B. "mc!")
    prefixes = [COMMAND_PREFIX]
    # Im Mirror-Channel zusätzlich das klassische "-" erlauben
    try:
        if CHAT_CHANNEL_ID_INT and message and message.channel and message.channel.id == CHAT_CHANNEL_ID_INT:
            prefixes.append("-")
    except Exception:
        pass
    return prefixes

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
    global COMMAND_PREFIX
    global COUNTDOWN_CHANNEL_ID_INT, COUNTDOWN_TARGET_ISO, COUNTDOWN_TZ, COUNTDOWN_LAST_EVENT_ID

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

    prefix_cfg = data.get("command_prefix")
    if isinstance(prefix_cfg, str) and prefix_cfg:
        COMMAND_PREFIX = prefix_cfg

    retention_cfg = _parse_int(str(data.get("message_cleanup_retention_hours")))
    if retention_cfg is not None:
        global MESSAGE_CLEANUP_RETENTION_HOURS_INT
        MESSAGE_CLEANUP_RETENTION_HOURS_INT = retention_cfg

    interval_cfg = _parse_int(str(data.get("message_cleanup_interval_minutes")))
    if interval_cfg is not None:
        global MESSAGE_CLEANUP_INTERVAL_MINUTES_INT
        MESSAGE_CLEANUP_INTERVAL_MINUTES_INT = interval_cfg

    # Countdown
    cd_channel = _parse_int(data.get("countdown_channel_id"))
    if cd_channel is not None:
        COUNTDOWN_CHANNEL_ID_INT = cd_channel
    cd_target = data.get("countdown_target_iso")
    if isinstance(cd_target, str) and cd_target:
        COUNTDOWN_TARGET_ISO = cd_target.strip()
    cd_tz = data.get("countdown_timezone")
    if isinstance(cd_tz, str) and cd_tz:
        COUNTDOWN_TZ = cd_tz.strip()
    last_id = data.get("countdown_last_event_id")
    if isinstance(last_id, str) and last_id:
        COUNTDOWN_LAST_EVENT_ID = last_id

    HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)
    HAS_GITHUB = bool(GITHUB_REPO and GITHUB_UPDATES_CHANNEL_ID_INT)

_apply_runtime_config(load_config())

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
                if not HAS_GITHUB or WEBHOOK_ACTIVE:
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

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(description="Discord Chatbot", command_prefix=get_command_prefix, intents=intents)


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
    if WEBHOOK_ACTIVE:
        bot.loop.create_task(start_web_server())
    # Auto-Cleanup-Job starten
    if CHAT_CHANNEL_ID_INT and (MESSAGE_CLEANUP_RETENTION_HOURS_INT or 0) > 0:
        bot.loop.create_task(message_cleanup_task())
    # Countdown-Job starten
    if COUNTDOWN_CHANNEL_ID_INT and COUNTDOWN_TARGET_ISO:
        bot.loop.create_task(countdown_task())
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


@bot.command(name='whitelistadd')
async def whitelistadd(ctx, *, arg):
    if CHAT_CHANNEL_ID_INT and ctx.channel.id != CHAT_CHANNEL_ID_INT:
        return
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
    if CHAT_CHANNEL_ID_INT and ctx.channel.id != CHAT_CHANNEL_ID_INT:
        return
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


@bot.command(name='wielange')
async def wielange(ctx):
    if not COUNTDOWN_TARGET_ISO:
        await ctx.send("Kein Countdown-Ziel gesetzt.")
        return
    tz = ZoneInfo(COUNTDOWN_TZ)
    now = datetime.now(tz)
    target = _parse_iso_to_aware_dt(COUNTDOWN_TARGET_ISO, COUNTDOWN_TZ)
    remaining = target - now
    if remaining.total_seconds() <= 0:
        await ctx.send("Der Zeitpunkt ist bereits erreicht.")
        return
    await ctx.send("Verbleibende Zeit: " + _format_time_delta(remaining))


#        if status['online'] == 0:
#            await ctx.send("Server is offline")
#        else:
#            res =


 # ------------------------------
 # Slash Commands (Konfiguration)
 # ------------------------------

@bot.tree.command(name="set_server_channel", description="Setzt den Discord-Channel für die Minecraft-Brücke")
@app_commands.describe(channel="Ziel-Channel für Brücke")
@app_commands.default_permissions(manage_guild=True)
async def set_server_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data = load_config()
    data["chat_channel_id"] = channel.id
    save_config(data)
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
    data = load_config()
    data["github_repo"] = repo
    data["github_updates_channel_id"] = channel.id
    if poll_interval_seconds and poll_interval_seconds > 0:
        data["github_poll_interval_seconds"] = poll_interval_seconds
    save_config(data)
    _apply_runtime_config(data)
    _last_seen_commit_sha = None
    await interaction.response.send_message(f"GitHub-Updates gesetzt: {repo} → {channel.mention}.", ephemeral=True)


@bot.tree.command(name="disable_github", description="Deaktiviert GitHub-Commit-Updates")
@app_commands.default_permissions(manage_guild=True)
async def disable_github(interaction: discord.Interaction):
    data = load_config()
    data.pop("github_repo", None)
    data.pop("github_updates_channel_id", None)
    save_config(data)
    _apply_runtime_config(data)
    await interaction.response.send_message("GitHub-Updates deaktiviert.", ephemeral=True)


@bot.tree.command(name="show_config", description="Zeigt die aktuelle Bot-Konfiguration")
@app_commands.default_permissions(manage_guild=True)
async def show_config(interaction: discord.Interaction):
    data = {
        "command_prefix": COMMAND_PREFIX,
        "bridge_channel_id": CHAT_CHANNEL_ID_INT,
        "github_repo": GITHUB_REPO,
        "github_updates_channel_id": GITHUB_UPDATES_CHANNEL_ID_INT,
        "github_poll_interval_seconds": GITHUB_POLL_INTERVAL,
        "message_cleanup_retention_hours": MESSAGE_CLEANUP_RETENTION_HOURS_INT,
        "message_cleanup_interval_minutes": MESSAGE_CLEANUP_INTERVAL_MINUTES_INT,
        "countdown_channel_id": COUNTDOWN_CHANNEL_ID_INT,
        "countdown_target_iso": COUNTDOWN_TARGET_ISO,
        "countdown_timezone": COUNTDOWN_TZ,
        "features": {
            "bridge": HAS_BRIDGE,
            "rcon": HAS_RCON,
            "query": HAS_QUERY,
            "github": HAS_GITHUB,
        },
    }
    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    await interaction.response.send_message(f"```json\n{pretty}\n```", ephemeral=True)


@bot.tree.command(name="change_prefix", description="Ändert das Bot-Prefix für Textcommands")
@app_commands.describe(prefix="Neues Prefix, z. B. ! oder --")
@app_commands.default_permissions(manage_guild=True)
async def change_prefix(interaction: discord.Interaction, prefix: str):
    global COMMAND_PREFIX
    prefix = prefix.strip()
    if not prefix:
        await interaction.response.send_message("Prefix darf nicht leer sein.", ephemeral=True)
        return
    if len(prefix) > 5:
        await interaction.response.send_message("Prefix ist zu lang (max. 5 Zeichen).", ephemeral=True)
        return
    data = load_config()
    data["command_prefix"] = prefix
    save_config(data)
    COMMAND_PREFIX = prefix
    await interaction.response.send_message(f"Prefix geändert auf `{prefix}`.", ephemeral=True)


@bot.tree.command(name="set_cleanup", description="Setzt Aufbewahrungsdauer und Laufintervall für Auto-Cleanup")
@app_commands.describe(retention_hours="Stunden bis zur Löschung (z. B. 48)", interval_minutes="Intervall in Minuten (z. B. 60)")
@app_commands.default_permissions(manage_guild=True)
async def set_cleanup(interaction: discord.Interaction, retention_hours: Optional[int] = None, interval_minutes: Optional[int] = None):
    changed = []
    data = load_config()
    if retention_hours is not None and retention_hours >= 0:
        data["message_cleanup_retention_hours"] = retention_hours
        changed.append(f"retention={retention_hours}h")
    if interval_minutes is not None and interval_minutes > 0:
        data["message_cleanup_interval_minutes"] = interval_minutes
        changed.append(f"interval={interval_minutes}m")
    if not changed:
        await interaction.response.send_message("Keine Änderungen übergeben.", ephemeral=True)
        return
    save_config(data)
    _apply_runtime_config(data)
    await interaction.response.send_message("Cleanup aktualisiert: " + ", ".join(changed), ephemeral=True)


@bot.tree.command(name="set_countdown", description="Setzt Countdown-Ziel (ISO Datum/Zeit) und Ziel-Channel")
@app_commands.describe(target_iso="z. B. 2025-12-31T17:00", channel="Ziel-Channel", timezone_name="z. B. Europe/Berlin")
@app_commands.default_permissions(manage_guild=True)
async def set_countdown(interaction: discord.Interaction, target_iso: str, channel: discord.TextChannel, timezone_name: Optional[str] = None):
    tzname = timezone_name.strip() if isinstance(timezone_name, str) and timezone_name else COUNTDOWN_TZ
    try:
        _ = _parse_iso_to_aware_dt(target_iso, tzname)
    except Exception:
        await interaction.response.send_message("Ungültiges ISO-Datum. Beispiel: 2025-12-31T17:00", ephemeral=True)
        return
    data = load_config()
    data["countdown_channel_id"] = channel.id
    data["countdown_target_iso"] = target_iso
    data["countdown_timezone"] = tzname
    save_config(data)
    _apply_runtime_config(data)
    await interaction.response.send_message(f"Countdown gesetzt: {target_iso} ({tzname}) → {channel.mention}", ephemeral=True)


@bot.tree.command(name="disable_countdown", description="Deaktiviert den Countdown")
@app_commands.default_permissions(manage_guild=True)
async def disable_countdown(interaction: discord.Interaction):
    data = load_config()
    data.pop("countdown_channel_id", None)
    data.pop("countdown_target_iso", None)
    data.pop("countdown_timezone", None)
    save_config(data)
    _apply_runtime_config(data)
    await interaction.response.send_message("Countdown deaktiviert.", ephemeral=True)


# ------------------------------
# Webhook Server (GitHub)
# ------------------------------

async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def github_webhook_handler(request: web.Request) -> web.Response:
    if not WEBHOOK_ACTIVE:
        return web.Response(status=404)
    signature = request.headers.get("X-Hub-Signature-256", "")
    event = request.headers.get("X-GitHub-Event", "")
    body = await request.read()
    expected = "sha256=" + hmac.new(GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return web.Response(status=401, text="invalid signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return web.Response(status=400, text="invalid json")
    if event == "push":
        repo_full_name = (payload.get("repository") or {}).get("full_name")
        if GITHUB_REPO and repo_full_name and GITHUB_REPO != repo_full_name:
            return web.Response(status=202, text="ignored repo")
        channel_id = GITHUB_UPDATES_CHANNEL_ID_INT
        if not channel_id:
            return web.Response(status=202, text="no channel configured")
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                return web.Response(status=202, text="channel not found")
        commits = payload.get("commits") or []
        if not commits and payload.get("head_commit"):
            commits = [payload.get("head_commit")]
        for c in commits:
            author = ((c.get("author") or {}).get("name")) or "?"
            message = c.get("message") or ""
            url = c.get("url") or ""
            await channel.send(f"[GitHub] {author}: {message}\n{url}")
        return web.Response(text="ok")
    return web.Response(text="ignored")

async def start_web_server() -> None:
    app = web.Application()
    app.add_routes([web.get("/healthz", handle_health), web.post("/github", github_webhook_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT") or 8080)
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Webhook Server listening on :%d", port)

async def message_cleanup_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if not CHAT_CHANNEL_ID_INT or (MESSAGE_CLEANUP_RETENTION_HOURS_INT or 0) <= 0:
                await asyncio.sleep(MESSAGE_CLEANUP_INTERVAL_MINUTES_INT * 60)
                continue
            channel = bot.get_channel(CHAT_CHANNEL_ID_INT)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(CHAT_CHANNEL_ID_INT)
                except Exception:
                    await asyncio.sleep(MESSAGE_CLEANUP_INTERVAL_MINUTES_INT * 60)
                    continue
            cutoff = datetime.now(timezone.utc) - timedelta(hours=MESSAGE_CLEANUP_RETENTION_HOURS_INT)
            async for msg in channel.history(limit=200, oldest_first=False):
                if msg.created_at and msg.created_at.replace(tzinfo=timezone.utc) < cutoff:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Cleanup Fehler: %s", exc)
        await asyncio.sleep(MESSAGE_CLEANUP_INTERVAL_MINUTES_INT * 60)

def _parse_iso_to_aware_dt(iso_str: str, tz_name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        # Fallback: jetzt + 1 Tag
        return (datetime.now(timezone.utc) + timedelta(days=1)).astimezone(ZoneInfo(tz_name))

def _format_time_delta(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} Tage")
    if hours:
        parts.append(f"{hours} Std")
    if minutes and not days:
        parts.append(f"{minutes} Min")
    return ", ".join(parts) or "0 Min"

async def countdown_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if not COUNTDOWN_CHANNEL_ID_INT or not COUNTDOWN_TARGET_ISO:
                await asyncio.sleep(300)
                continue
            channel = bot.get_channel(COUNTDOWN_CHANNEL_ID_INT)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(COUNTDOWN_CHANNEL_ID_INT)
                except Exception:
                    await asyncio.sleep(300)
                    continue
            tz = ZoneInfo(COUNTDOWN_TZ)
            now = datetime.now(tz)
            target = _parse_iso_to_aware_dt(COUNTDOWN_TARGET_ISO, COUNTDOWN_TZ)
            remaining = target - now

            # Ermittlung der Sendelogik
            if remaining.total_seconds() <= 0:
                await asyncio.sleep(600)
                continue

            send_now = False
            message = None

            # Weniger als 7 Tage → täglich zur Zieluhrzeit
            if remaining <= timedelta(days=7):
                if now.hour == target.hour and now.minute == target.minute:
                    if remaining > timedelta(hours=24):
                        days_left = remaining.days
                        message = f"Es sind noch {days_left} Tage bis zum Serverstart verbleibend."
                        send_now = True
                    else:
                        # Am Starttag besondere Intervalle
                        # 00:00, 12:00 und dann 3h/2h/1h/10min vor Start
                        midnight = target.replace(hour=0, minute=0, second=0, microsecond=0)
                        if now >= midnight:
                            hours_left = int(remaining.total_seconds() // 3600)
                            if now.hour == 0 and now.minute == 0:
                                message = f"Heute ist Start! Noch {hours_left} Stunden."
                                send_now = True
                            elif now.hour == 12 and now.minute == 0:
                                message = f"Heute ist Start! Noch {hours_left} Stunden."
                                send_now = True
                            else:
                                # 3h, 2h, 1h, 10min vorher
                                checkpoints = [
                                    timedelta(hours=3),
                                    timedelta(hours=2),
                                    timedelta(hours=1),
                                    timedelta(minutes=10),
                                ]
                                for cp in checkpoints:
                                    if abs((remaining - cp).total_seconds()) < 60:
                                        if cp >= timedelta(hours=1):
                                            message = f"Nur noch {int(cp.total_seconds()//3600)} Stunden bis zum Start!"
                                        else:
                                            message = "Nur noch 10 Minuten bis zum Start!"
                                        send_now = True
                                        break
            else:
                # Wöchentlich am Wochentag/Uhrzeit des Targets
                if now.weekday() == target.weekday() and now.hour == target.hour and now.minute == target.minute:
                    weeks_left = int(remaining.days // 7)
                    if weeks_left < 1:
                        weeks_left = 1
                    message = f"Es sind noch {weeks_left} Wochen bis zum Serverstart verbleibend."
                    send_now = True

            if send_now and message:
                try:
                    await channel.send(message)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Countdown Fehler: %s", exc)
        await asyncio.sleep(60)
bot.run(TOKEN)
