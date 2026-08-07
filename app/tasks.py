"""Hintergrund-Tasks.

Alle Tasks laufen dauerhaft und lesen die Config bei jedem Durchlauf neu.
Nicht konfigurierte Features schlafen einfach — dadurch greifen Änderungen
per Slash-Command ohne Neustart.
"""

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger("betterMCbot.tasks")


def parse_iso_to_aware_dt(iso_str, tz_name):
    tz = ZoneInfo(tz_name)
    try:
        dt = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).astimezone(tz) + timedelta(days=1)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def format_time_delta(delta):
    """Gerundet — für automatische Countdown-Nachrichten."""
    total_seconds = max(delta.total_seconds(), 0)
    if total_seconds >= 86400:
        return f"{math.ceil(total_seconds / 86400)} Tage"
    if total_seconds >= 3600:
        return f"{math.ceil(total_seconds / 3600)} Std"
    return f"{int(total_seconds / 60)} Min"


def format_precise_delta(delta):
    """Exakt — für mc!wielange, wo auf Nachfrage die genaue Zeit erwartet wird."""
    total_seconds = max(int(delta.total_seconds()), 0)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    return f"{days} Tage, {hours} Stunden, {remainder // 60} Minuten"


async def resolve_channel(bot, channel_id):
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def delete_message(channel, message_id, bot_only=True, bot=None):
    if not message_id:
        return
    try:
        message = await channel.fetch_message(message_id)
        if message and (not bot_only or message.author == bot.user):
            await message.delete()
    except Exception:
        pass


async def github_updates_task(bot, cfg):
    """Polling-Fallback. Inaktiv, solange GITHUB_WEBHOOK_SECRET gesetzt ist."""
    await bot.wait_until_ready()
    last_seen_sha = None
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            interval = cfg.github_poll_interval_seconds
            try:
                if cfg.has_github and not cfg.github_webhook_secret:
                    channel = await resolve_channel(bot, cfg.github_updates_channel_id)
                    if channel is not None:
                        commits = await _fetch_commits(session, cfg.github_repo)
                        last_seen_sha = await _post_new_commits(channel, commits, last_seen_sha)
            except Exception as exc:
                logger.warning("GitHub-Updates fehlgeschlagen: %s", exc)
            await asyncio.sleep(interval)


async def _fetch_commits(session, repo):
    url = f"https://api.github.com/repos/{repo}/commits"
    async with session.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub API {resp.status}: {await resp.text()}")
        return await resp.json()


async def _post_new_commits(channel, commits, last_seen_sha):
    if not isinstance(commits, list) or not commits:
        return last_seen_sha
    newest_sha = commits[0].get("sha")
    if last_seen_sha is None or newest_sha == last_seen_sha:
        return newest_sha
    new_items = []
    for item in commits:
        if item.get("sha") == last_seen_sha:
            break
        new_items.append(item)
    for item in reversed(new_items):
        commit = item.get("commit", {})
        author = commit.get("author", {}).get("name", "?")
        await channel.send(f"[GitHub] {author}: {commit.get('message', '')}\n{item.get('html_url', '')}")
    return newest_sha


async def message_cleanup_task(bot, cfg):
    await bot.wait_until_ready()
    while not bot.is_closed():
        retention = cfg.message_cleanup_retention_hours or 0
        try:
            if cfg.chat_channel_id and retention > 0:
                channel = await resolve_channel(bot, cfg.chat_channel_id)
                if channel is not None:
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention)
                    async for msg in channel.history(limit=200, oldest_first=False):
                        if msg.created_at and msg.created_at.replace(tzinfo=timezone.utc) < cutoff:
                            try:
                                await msg.delete()
                            except Exception:
                                pass
        except Exception as exc:
            logger.warning("Cleanup fehlgeschlagen: %s", exc)
        await asyncio.sleep((cfg.message_cleanup_interval_minutes or 60) * 60)


def countdown_message(now, target, remaining):
    """Gibt die fällige Countdown-Nachricht zurück, sonst None.

    Wird jede Minute aufgerufen; die Zeitfenster sind minutengenau.
    """
    if remaining > timedelta(days=7):
        if (now.weekday(), now.hour, now.minute) == (target.weekday(), target.hour, target.minute):
            weeks = max(math.ceil(remaining.total_seconds() / (7 * 86400)), 1)
            return f"Es sind noch {weeks} Wochen bis zum Serverstart verbleibend."
        return None

    if (now.hour, now.minute) == (target.hour, target.minute) and remaining > timedelta(hours=24):
        days = math.ceil(remaining.total_seconds() / 86400)
        return f"Es sind noch {days} Tage bis zum Serverstart verbleibend."

    if remaining > timedelta(hours=24):
        return None

    # Starttag
    if now < target.replace(hour=0, minute=0, second=0, microsecond=0):
        return None
    if (now.hour, now.minute) == (0, 0):
        return f"Heute ist Start! Noch {math.ceil(remaining.total_seconds() / 3600)} Stunden."
    # 12h relativ zum Ziel statt fix um 12:00 — sonst hängt der Abstand zur
    # Startzeit davon ab, wann am Tag der Server startet.
    for checkpoint in (
        timedelta(hours=12),
        timedelta(hours=3),
        timedelta(hours=2),
        timedelta(hours=1),
        timedelta(minutes=10),
    ):
        if abs((remaining - checkpoint).total_seconds()) < 60:
            if checkpoint >= timedelta(hours=1):
                return f"Nur noch {math.ceil(checkpoint.total_seconds() / 3600)} Stunden bis zum Start!"
            return "Nur noch 10 Minuten bis zum Start!"
    return None


def countdown_reached(cfg):
    """True, wenn das Startdatum erreicht ist — oder gar kein Countdown läuft."""
    if not cfg.countdown_target_iso:
        return True
    tz_name = cfg.countdown_timezone
    return parse_iso_to_aware_dt(cfg.countdown_target_iso, tz_name) <= datetime.now(ZoneInfo(tz_name))


def render_timer_message(message, address):
    """Setzt die Server-Adresse in die Timer-Nachricht ein.

    `{ip}` bestimmt die Position; ohne Platzhalter wird die Adresse angehängt.
    """
    if not address:
        return message.replace("{ip}", "").rstrip()
    if "{ip}" in message:
        return message.replace("{ip}", address)
    return f"{message}\nIP: `{address}`"


async def countdown_task(bot, cfg):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await _countdown_tick(bot, cfg)
        except Exception as exc:
            logger.warning("Countdown fehlgeschlagen: %s", exc)
        await asyncio.sleep(60)


async def _countdown_tick(bot, cfg):
    if not (cfg.countdown_channel_id and cfg.countdown_target_iso):
        return
    channel = await resolve_channel(bot, cfg.countdown_channel_id)
    if channel is None:
        return

    tz_name = cfg.countdown_timezone
    now = datetime.now(ZoneInfo(tz_name))
    target = parse_iso_to_aware_dt(cfg.countdown_target_iso, tz_name)
    remaining = target - now

    if remaining.total_seconds() <= 0:
        if cfg.countdown_timer_message and not cfg.countdown_timer_message_sent:
            await channel.send(render_timer_message(cfg.countdown_timer_message, cfg.server_address))
            cfg.set(countdown_timer_message_sent=True)
            logger.info("Timer-Nachricht gesendet")
        return

    message = countdown_message(now, target, remaining)
    if not message:
        return

    await delete_message(channel, cfg.countdown_last_auto_message_id, bot=bot)
    role_id = cfg.countdown_role_id
    if role_id:
        message = f"<@&{role_id}> {message}"
    sent = await channel.send(message)
    cfg.set(countdown_last_auto_message_id=sent.id)
