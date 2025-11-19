import os

# Voice-Funktionen deaktivieren, um audioop-Import zu vermeiden (z. B. unter Python 3.13)
os.environ.setdefault("DISCORD_DISABLE_VOICE", "1")

import discord
from discord.ext import commands
from dotenv import load_dotenv
from mcipc.rcon.je import Client
from mcipc.query import Client as QueryClient
import asyncio
import aiohttp
import logging
import json
import random
from typing import Optional
from app.settings import load_config, save_config
from app.tasks import (
    github_updates_task as task_github_updates,
    message_cleanup_task as task_cleanup,
    start_web_server as task_start_web,
    countdown_task as task_countdown,
    parse_iso_to_aware_dt as task_parse_iso,
    format_time_delta as task_fmt_td,
)
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from app.commands import register_text_commands, register_slash_commands

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

DEATH_CHAT_RESPONSES = [
    "F",
    "ooof",
    "RIP",
    "The dead ones are the deadliest",
]

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
COUNTDOWN_LAST_MESSAGE_ID = None
COUNTDOWN_LAST_AUTO_MESSAGE_ID = None
COUNTDOWN_LAST_TRIGGER_ID = None
COUNTDOWN_ROLE_ID_INT = None

HAS_RCON = bool(SERVER_IP and RCON_PASSWORD and RCON_PORT_INT)
HAS_QUERY = bool(SERVER_IP and QUERY_PORT_INT)
HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)
HAS_GITHUB = bool(GITHUB_REPO and GITHUB_UPDATES_CHANNEL_ID_INT)

_last_seen_commit_sha = None
 

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

 

def _apply_runtime_config(data):
    global CHAT_CHANNEL_ID_INT, GITHUB_REPO, GITHUB_UPDATES_CHANNEL_ID_INT, GITHUB_POLL_INTERVAL
    global HAS_BRIDGE, HAS_GITHUB
    global COMMAND_PREFIX
    global COUNTDOWN_CHANNEL_ID_INT, COUNTDOWN_TARGET_ISO, COUNTDOWN_TZ, COUNTDOWN_LAST_EVENT_ID, COUNTDOWN_LAST_MESSAGE_ID, COUNTDOWN_LAST_AUTO_MESSAGE_ID, COUNTDOWN_LAST_TRIGGER_ID, COUNTDOWN_ROLE_ID_INT

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
    else:
        # Fallback auf ENV (für Erststart), falls vorhanden
        env_target = os.getenv("COUNTDOWN_TARGET_ISO")
        if env_target:
            COUNTDOWN_TARGET_ISO = env_target.strip()
    cd_tz = data.get("countdown_timezone")
    if isinstance(cd_tz, str) and cd_tz:
        COUNTDOWN_TZ = cd_tz.strip()
    else:
        COUNTDOWN_TZ = os.getenv("TIMEZONE", COUNTDOWN_TZ)
    last_id = data.get("countdown_last_event_id")
    if isinstance(last_id, str) and last_id:
        COUNTDOWN_LAST_EVENT_ID = last_id
    last_msg_id = data.get("countdown_last_message_id")
    if last_msg_id is not None:
        try:
            COUNTDOWN_LAST_MESSAGE_ID = int(last_msg_id)
        except Exception:
            COUNTDOWN_LAST_MESSAGE_ID = None
    last_auto_msg_id = data.get("countdown_last_auto_message_id")
    if last_auto_msg_id is not None:
        try:
            COUNTDOWN_LAST_AUTO_MESSAGE_ID = int(last_auto_msg_id)
        except Exception:
            COUNTDOWN_LAST_AUTO_MESSAGE_ID = None
    last_trig_id = data.get("countdown_last_trigger_id")
    if last_trig_id is not None:
        try:
            COUNTDOWN_LAST_TRIGGER_ID = int(last_trig_id)
        except Exception:
            COUNTDOWN_LAST_TRIGGER_ID = None

    role_id = data.get("countdown_role_id")
    try:
        COUNTDOWN_ROLE_ID_INT = _parse_int(str(role_id)) if role_id is not None else None
    except Exception:
        COUNTDOWN_ROLE_ID_INT = None

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
        bot.loop.create_task(task_github_updates(bot, logger, fetch_latest_commits, {
            "HAS_GITHUB": HAS_GITHUB,
            "WEBHOOK_ACTIVE": WEBHOOK_ACTIVE,
            "GITHUB_POLL_INTERVAL": GITHUB_POLL_INTERVAL,
            "GITHUB_UPDATES_CHANNEL_ID_INT": GITHUB_UPDATES_CHANNEL_ID_INT,
            "GITHUB_REPO": GITHUB_REPO,
        }))
    if WEBHOOK_ACTIVE:
        async def verify_and_handle_github(request):
            import hmac, hashlib
            signature = request.headers.get("X-Hub-Signature-256", "")
            event = request.headers.get("X-GitHub-Event", "")
            body = await request.read()
            expected = "sha256=" + hmac.new(GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                from aiohttp import web
                return web.Response(status=401, text="invalid signature")
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                from aiohttp import web
                return web.Response(status=400, text="invalid json")
            if event == "push":
                repo_full_name = (payload.get("repository") or {}).get("full_name")
                if GITHUB_REPO and repo_full_name and GITHUB_REPO != repo_full_name:
                    from aiohttp import web
                    return web.Response(status=202, text="ignored repo")
                channel_id = GITHUB_UPDATES_CHANNEL_ID_INT
                if not channel_id:
                    from aiohttp import web
                    return web.Response(status=202, text="no channel configured")
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception:
                        from aiohttp import web
                        return web.Response(status=202, text="channel not found")
                commits = payload.get("commits") or []
                if not commits and payload.get("head_commit"):
                    commits = [payload.get("head_commit")]
                for c in commits:
                    author = ((c.get("author") or {}).get("name")) or "?"
                    message = c.get("message") or ""
                    url = c.get("url") or ""
                    await channel.send(f"[GitHub] {author}: {message}\n{url}")
                from aiohttp import web
                return web.Response(text="ok")
            elif event == "pull_request":
                repo_full_name = (payload.get("repository") or {}).get("full_name")
                if GITHUB_REPO and repo_full_name and GITHUB_REPO != repo_full_name:
                    from aiohttp import web
                    return web.Response(status=202, text="ignored repo")
                channel_id = GITHUB_UPDATES_CHANNEL_ID_INT
                if not channel_id:
                    from aiohttp import web
                    return web.Response(status=202, text="no channel configured")
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception:
                        from aiohttp import web
                        return web.Response(status=202, text="channel not found")
                action = payload.get("action", "")
                pr = payload.get("pull_request") or {}
                pr_number = pr.get("number", "?")
                pr_title = pr.get("title", "Unbekannt")
                pr_url = pr.get("html_url", "")
                pr_user = (pr.get("user") or {}).get("login", "?")
                pr_state = pr.get("state", "")
                
                # Nachrichten für verschiedene PR-Aktionen
                if action == "opened":
                    msg = f"🔔 **Neue Pull Request #{pr_number}** von **{pr_user}**\n**Titel:** {pr_title}\n{pr_url}"
                elif action == "closed":
                    if pr.get("merged", False):
                        merged_by = (pr.get("merged_by") or {}).get("login", "?")
                        msg = f"✅ **Pull Request #{pr_number} gemerged** von **{merged_by}**\n**Titel:** {pr_title}\n{pr_url}"
                    else:
                        msg = f"❌ **Pull Request #{pr_number} geschlossen** (nicht gemerged)\n**Titel:** {pr_title}\n{pr_url}"
                elif action == "reopened":
                    msg = f"🔄 **Pull Request #{pr_number} wiedereröffnet** von **{pr_user}**\n**Titel:** {pr_title}\n{pr_url}"
                elif action == "ready_for_review":
                    msg = f"👀 **Pull Request #{pr_number} ist bereit für Review**\n**Titel:** {pr_title}\n{pr_url}"
                elif action == "review_requested":
                    requested_reviewer = (payload.get("requested_reviewer") or {}).get("login", "?")
                    msg = f"👥 **Review angefordert** für PR #{pr_number} von **{requested_reviewer}**\n**Titel:** {pr_title}\n{pr_url}"
                else:
                    # Andere Aktionen ignorieren oder generisch behandeln
                    from aiohttp import web
                    return web.Response(status=202, text=f"ignored action: {action}")
                
                await channel.send(msg)
                from aiohttp import web
                return web.Response(text="ok")
            from aiohttp import web
            return web.Response(text="ignored")
        async def verify_and_handle_mc(request):
            from aiohttp import web
            mc_secret = os.getenv("MC_WEBHOOK_SECRET")
            if not mc_secret:
                return web.Response(status=404)
            sig = request.headers.get("X-MC-Signature", "")
            body = await request.read()
            expected = "sha256=" + __import__("hashlib").sha256((mc_secret).encode("utf-8") + body).hexdigest()
            if sig != expected:
                return web.Response(status=401, text="invalid signature")
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                return web.Response(status=400, text="invalid json")

            event = payload.get("event")
            content = payload.get("content") or ""
            channel_id = CHAT_CHANNEL_ID_INT
            if not channel_id:
                return web.Response(status=202, text="no mirror channel")
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except Exception:
                    return web.Response(status=202, text="channel not found")
            if event == "chat":
                author = payload.get("author") or "MC"
                await channel.send(f"[MC] {author}: {content}")
            elif event == "join":
                await channel.send(f"[MC] {content} ist beigetreten")
            elif event == "leave":
                await channel.send(f"[MC] {content} hat den Server verlassen")
            elif event == "death":
                player = payload.get("player") or payload.get("author")
                death_details = content.strip() if isinstance(content, str) else ""
                if player and death_details:
                    discord_msg = f"[MC] 💀 {player} ist gestorben: {death_details}"
                elif player:
                    discord_msg = f"[MC] 💀 {player} ist gestorben."
                else:
                    discord_msg = f"[MC] 💀 {death_details or 'Ein Spieler ist gestorben.'}"
                await channel.send(discord_msg)
                if HAS_RCON:
                    try:
                        with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
                            reply = random.choice(DEATH_CHAT_RESPONSES)
                            client.say(f"[Bot] {reply}")
                    except Exception as exc:
                        logger.warning("RCON Death Reply fehlgeschlagen: %s", exc)
            elif event == "whitelistadd":
                # optional, kann Client auslösen
                try:
                    with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
                        wl = client.whitelist
                        wl.add(str(content))
                except Exception:
                    pass
            return web.Response(text="ok")
        bot.loop.create_task(task_start_web(bot, logger, {"PORT": os.getenv("PORT")}, verify_and_handle_github, verify_and_handle_mc))
    # Auto-Cleanup-Job starten (zentrale Implementierung aus app.tasks nutzen)
    if CHAT_CHANNEL_ID_INT and (MESSAGE_CLEANUP_RETENTION_HOURS_INT or 0) > 0:
        bot.loop.create_task(task_cleanup(bot, logger, {
            "CHAT_CHANNEL_ID_INT": CHAT_CHANNEL_ID_INT,
            "MESSAGE_CLEANUP_RETENTION_HOURS_INT": MESSAGE_CLEANUP_RETENTION_HOURS_INT,
            "MESSAGE_CLEANUP_INTERVAL_MINUTES_INT": MESSAGE_CLEANUP_INTERVAL_MINUTES_INT,
        }))
    # Countdown-Job starten
    if COUNTDOWN_CHANNEL_ID_INT and COUNTDOWN_TARGET_ISO:
        bot.loop.create_task(task_countdown(
            bot,
            logger,
            {
                "COUNTDOWN_CHANNEL_ID_INT": COUNTDOWN_CHANNEL_ID_INT,
                "COUNTDOWN_TARGET_ISO": COUNTDOWN_TARGET_ISO,
                "COUNTDOWN_TZ": COUNTDOWN_TZ,
                "COUNTDOWN_ROLE_ID_INT": COUNTDOWN_ROLE_ID_INT,
            },
            task_parse_iso,
            task_fmt_td,
            lambda: COUNTDOWN_LAST_AUTO_MESSAGE_ID,
            lambda mid: _save_last_countdown_auto_message_id(mid)
        ))
    # Commands registrieren
    deps = {
        "mcipc_Client": Client,
        "QueryClient": QueryClient,
        "CHAT_CHANNEL_ID_INT": CHAT_CHANNEL_ID_INT,
        "HAS_RCON": HAS_RCON,
        "SERVER_IP": SERVER_IP,
        "RCON_PORT_INT": RCON_PORT_INT,
        "RCON_PASSWORD": RCON_PASSWORD,
        "HAS_QUERY": HAS_QUERY,
        "QUERY_PORT_INT": QUERY_PORT_INT,
        "COUNTDOWN_TARGET_ISO": COUNTDOWN_TARGET_ISO,
        "ZoneInfo": ZoneInfo,
        "datetime": datetime,
        "parse_iso_to_dt": task_parse_iso,
        "COUNTDOWN_TZ": COUNTDOWN_TZ,
        "fmt_td": task_fmt_td,
        "get_last_msg_id": lambda: COUNTDOWN_LAST_MESSAGE_ID,
        "set_last_msg_id": lambda mid: _save_last_countdown_message_id(mid),
        "get_last_auto_msg_id": lambda: COUNTDOWN_LAST_AUTO_MESSAGE_ID,
        "get_last_trigger_id": lambda: COUNTDOWN_LAST_TRIGGER_ID,
        "set_last_trigger_id": lambda mid: _save_last_countdown_trigger_id(mid),
        "load_config": load_config,
        "save_config": save_config,
        "apply_config": _apply_runtime_config,
        "collect_config_display": lambda: json.dumps({
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
            "countdown_role_id": COUNTDOWN_ROLE_ID_INT,
            "countdown_last_message_id": COUNTDOWN_LAST_MESSAGE_ID,
            "countdown_last_auto_message_id": COUNTDOWN_LAST_AUTO_MESSAGE_ID,
            "features": {
                "bridge": HAS_BRIDGE,
                "rcon": HAS_RCON,
                "query": HAS_QUERY,
                "github": HAS_GITHUB,
            },
        }, ensure_ascii=False, indent=2),
        "reset_last_commit": lambda: None,
    }
    register_text_commands(bot, deps)
    register_slash_commands(bot, deps)
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




#        if status['online'] == 0:
#            await ctx.send("Server is offline")
#        else:
#            res =



def _save_last_countdown_message_id(mid: int) -> None:
    global COUNTDOWN_LAST_MESSAGE_ID
    COUNTDOWN_LAST_MESSAGE_ID = mid
    data = load_config()
    data["countdown_last_message_id"] = mid
    save_config(data)

def _save_last_countdown_auto_message_id(mid: int) -> None:
    global COUNTDOWN_LAST_AUTO_MESSAGE_ID
    COUNTDOWN_LAST_AUTO_MESSAGE_ID = mid
    data = load_config()
    data["countdown_last_auto_message_id"] = mid
    save_config(data)

def _save_last_countdown_trigger_id(mid: int) -> None:
    global COUNTDOWN_LAST_TRIGGER_ID
    COUNTDOWN_LAST_TRIGGER_ID = mid
    data = load_config()
    data["countdown_last_trigger_id"] = mid
    save_config(data)

bot.run(TOKEN)
