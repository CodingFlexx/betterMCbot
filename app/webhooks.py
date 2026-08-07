"""HTTP-Endpunkte: /healthz, /github, /mc.

Läuft nur, wenn GITHUB_WEBHOOK_SECRET oder MC_WEBHOOK_SECRET gesetzt ist.
Hinter NAT (Homeserver) einfach beide leer lassen — dann greift GitHub-Polling.
"""

import hashlib
import hmac
import json
import logging
import random

from aiohttp import web
from mcipc.rcon.je import Client as RconClient

logger = logging.getLogger("betterMCbot.webhooks")

DEATH_CHAT_RESPONSES = [
    "F",
    "ooof",
    "RIP",
    "The dead ones are the deadliest",
]


async def _resolve_channel(bot, channel_id):
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


def _format_pull_request(payload):
    action = payload.get("action", "")
    pr = payload.get("pull_request") or {}
    number = pr.get("number", "?")
    title = pr.get("title", "Unbekannt")
    url = pr.get("html_url", "")
    user = (pr.get("user") or {}).get("login", "?")

    if action == "opened":
        return f"🔔 **Neue Pull Request #{number}** von **{user}**\n**Titel:** {title}\n{url}"
    if action == "closed":
        if pr.get("merged", False):
            merged_by = (pr.get("merged_by") or {}).get("login", "?")
            return f"✅ **Pull Request #{number} gemerged** von **{merged_by}**\n**Titel:** {title}\n{url}"
        return f"❌ **Pull Request #{number} geschlossen** (nicht gemerged)\n**Titel:** {title}\n{url}"
    if action == "reopened":
        return f"🔄 **Pull Request #{number} wiedereröffnet** von **{user}**\n**Titel:** {title}\n{url}"
    if action == "ready_for_review":
        return f"👀 **Pull Request #{number} ist bereit für Review**\n**Titel:** {title}\n{url}"
    if action == "review_requested":
        reviewer = (payload.get("requested_reviewer") or {}).get("login", "?")
        return f"👥 **Review angefordert** für PR #{number} von **{reviewer}**\n**Titel:** {title}\n{url}"
    return None


async def _handle_github(bot, cfg, request):
    if not cfg.github_webhook_secret:
        return web.Response(status=404)

    body = await request.read()
    expected = "sha256=" + hmac.new(
        cfg.github_webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
        return web.Response(status=401, text="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return web.Response(status=400, text="invalid json")

    event = request.headers.get("X-GitHub-Event", "")
    if event not in ("push", "pull_request"):
        return web.Response(text="ignored")

    repo_full_name = (payload.get("repository") or {}).get("full_name")
    if cfg.github_repo and repo_full_name and cfg.github_repo != repo_full_name:
        return web.Response(status=202, text="ignored repo")

    channel = await _resolve_channel(bot, cfg.github_updates_channel_id)
    if channel is None:
        return web.Response(status=202, text="no channel")

    if event == "push":
        commits = payload.get("commits") or []
        if not commits and payload.get("head_commit"):
            commits = [payload["head_commit"]]
        for commit in commits:
            author = (commit.get("author") or {}).get("name") or "?"
            await channel.send(
                f"[GitHub] {author}: {commit.get('message') or ''}\n{commit.get('url') or ''}"
            )
        return web.Response(text="ok")

    message = _format_pull_request(payload)
    if message is None:
        return web.Response(status=202, text="ignored action")
    await channel.send(message)
    return web.Response(text="ok")


async def _handle_mc(bot, cfg, request):
    if not cfg.mc_webhook_secret:
        return web.Response(status=404)

    body = await request.read()
    # Kein echtes HMAC — Format ist vom Bridge-Mod vorgegeben, nicht ändern
    # ohne den Mod mitzuziehen.
    expected = "sha256=" + hashlib.sha256(
        cfg.mc_webhook_secret.encode("utf-8") + body
    ).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-MC-Signature", ""), expected):
        return web.Response(status=401, text="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return web.Response(status=400, text="invalid json")

    event = payload.get("event")
    content = payload.get("content") or ""

    if event == "whitelistadd":
        if cfg.has_rcon:
            try:
                with RconClient(cfg.server_ip, cfg.rcon_port, passwd=cfg.rcon_password) as client:
                    client.whitelist.add(str(content))
            except Exception as exc:
                logger.warning("RCON Whitelist fehlgeschlagen: %s", exc)
        return web.Response(text="ok")

    channel = await _resolve_channel(bot, cfg.chat_channel_id)
    if channel is None:
        return web.Response(status=202, text="no mirror channel")

    if event == "chat":
        await channel.send(f"[MC] {payload.get('author') or 'MC'}: {content}")
    elif event == "join":
        await channel.send(f"[MC] {content} ist beigetreten")
    elif event == "leave":
        await channel.send(f"[MC] {content} hat den Server verlassen")
    elif event == "death":
        player = payload.get("player") or payload.get("author")
        details = content.strip() if isinstance(content, str) else ""
        if player and details:
            await channel.send(f"[MC] 💀 {player} ist gestorben: {details}")
        elif player:
            await channel.send(f"[MC] 💀 {player} ist gestorben.")
        else:
            await channel.send(f"[MC] 💀 {details or 'Ein Spieler ist gestorben.'}")
        if cfg.has_rcon:
            try:
                with RconClient(cfg.server_ip, cfg.rcon_port, passwd=cfg.rcon_password) as client:
                    client.say(f"[Bot] {random.choice(DEATH_CHAT_RESPONSES)}")
            except Exception as exc:
                logger.warning("RCON Death Reply fehlgeschlagen: %s", exc)

    return web.Response(text="ok")


async def start_web_server(bot, cfg):
    app = web.Application()
    app.add_routes([
        web.get("/healthz", lambda request: web.Response(text="ok")),
        web.post("/github", lambda request: _handle_github(bot, cfg, request)),
        web.post("/mc", lambda request: _handle_mc(bot, cfg, request)),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", cfg.webhook_port).start()
    logger.info("Webhook-Server lauscht auf :%d", cfg.webhook_port)
