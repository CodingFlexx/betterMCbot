import asyncio
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import aiohttp
from aiohttp import web

async def github_updates_task(bot, logger, fetch_latest_commits, cfg):
    _last_seen_commit_sha = None
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            try:
                if not cfg["HAS_GITHUB"] or cfg["WEBHOOK_ACTIVE"]:
                    await asyncio.sleep(cfg["GITHUB_POLL_INTERVAL"])
                    continue
                channel = bot.get_channel(cfg["GITHUB_UPDATES_CHANNEL_ID_INT"])
                if channel is None:
                    await asyncio.sleep(cfg["GITHUB_POLL_INTERVAL"])
                    continue
                commits = await fetch_latest_commits(session, cfg["GITHUB_REPO"])
                if not isinstance(commits, list) or not commits:
                    await asyncio.sleep(cfg["GITHUB_POLL_INTERVAL"])
                    continue
                newest = commits[0]
                sha = newest.get("sha")
                if _last_seen_commit_sha is None:
                    _last_seen_commit_sha = sha
                elif sha != _last_seen_commit_sha:
                    new_items = []
                    for item in commits:
                        if item.get("sha") == _last_seen_commit_sha:
                            break
                        new_items.append(item)
                    for item in reversed(new_items):
                        commit = item.get("commit", {})
                        author = commit.get("author", {}).get("name", "?")
                        message = commit.get("message", "")
                        url = item.get("html_url", "")
                        await channel.send(f"[GitHub] {author}: {message}\n{url}")
                    _last_seen_commit_sha = sha
            except Exception as exc:
                logger.warning("GitHub Updates Fehler: %s", exc)
            await asyncio.sleep(cfg["GITHUB_POLL_INTERVAL"])


async def message_cleanup_task(bot, logger, cfg):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if not cfg["CHAT_CHANNEL_ID_INT"] or (cfg["MESSAGE_CLEANUP_RETENTION_HOURS_INT"] or 0) <= 0:
                await asyncio.sleep(cfg["MESSAGE_CLEANUP_INTERVAL_MINUTES_INT"] * 60)
                continue
            channel = bot.get_channel(cfg["CHAT_CHANNEL_ID_INT"])
            if channel is None:
                try:
                    channel = await bot.fetch_channel(cfg["CHAT_CHANNEL_ID_INT"])
                except Exception:
                    await asyncio.sleep(cfg["MESSAGE_CLEANUP_INTERVAL_MINUTES_INT"] * 60)
                    continue
            cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["MESSAGE_CLEANUP_RETENTION_HOURS_INT"])
            async for msg in channel.history(limit=200, oldest_first=False):
                if msg.created_at and msg.created_at.replace(tzinfo=timezone.utc) < cutoff:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Cleanup Fehler: %s", exc)
        await asyncio.sleep(cfg["MESSAGE_CLEANUP_INTERVAL_MINUTES_INT"] * 60)


async def start_web_server(bot, logger, cfg, verify_and_handle_github, verify_and_handle_mc=None):
    async def handle_health(request: web.Request):
        return web.Response(text="ok")

    async def github_webhook_handler(request: web.Request):
        return await verify_and_handle_github(request)

    async def mc_webhook_handler(request: web.Request):
        if verify_and_handle_mc is None:
            return web.Response(status=404)
        return await verify_and_handle_mc(request)

    app = web.Application()
    routes = [web.get("/healthz", handle_health), web.post("/github", github_webhook_handler)]
    if verify_and_handle_mc is not None:
        routes.append(web.post("/mc", mc_webhook_handler))
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(cfg.get("PORT") or 8080)
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Webhook Server listening on :%d", port)


def parse_iso_to_aware_dt(dt_module_datetime, iso_str: str, tz_name: str) -> datetime:
    try:
        dt = dt_module_datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return (dt_module_datetime.now(timezone.utc) + timedelta(days=1)).astimezone(ZoneInfo(tz_name))


def format_time_delta(delta: timedelta) -> str:
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        total_seconds = 0
    
    # Aufrunden für Tage und Stunden, aber nicht für Minuten
    total_minutes = total_seconds / 60
    total_hours = total_seconds / 3600
    total_days = total_seconds / 86400
    
    # Wenn mehr als 1 Tag: Tage aufrunden und nur Tage anzeigen
    if total_days >= 1:
        days = math.ceil(total_days)
        return f"{days} Tage"
    
    # Wenn mehr als 1 Stunde aber weniger als 1 Tag: Stunden aufrunden
    if total_hours >= 1:
        hours = math.ceil(total_hours)
        return f"{hours} Std"
    
    # Wenn weniger als 1 Stunde: Exakte Minuten (abgerundet)
    minutes = int(total_minutes)
    return f"{minutes} Min" if minutes > 0 else "0 Min"


async def countdown_task(bot, logger, cfg, parse_iso_to_dt, fmt_td, get_last_msg_id, set_last_msg_id, get_timer_message_sent=None, set_timer_message_sent=None):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if not cfg["COUNTDOWN_CHANNEL_ID_INT"] or not cfg["COUNTDOWN_TARGET_ISO"]:
                await asyncio.sleep(300)
                continue
            channel = bot.get_channel(cfg["COUNTDOWN_CHANNEL_ID_INT"])
            if channel is None:
                try:
                    channel = await bot.fetch_channel(cfg["COUNTDOWN_CHANNEL_ID_INT"])
                except Exception:
                    await asyncio.sleep(300)
                    continue
            tz = ZoneInfo(cfg["COUNTDOWN_TZ"])
            now = datetime.now(tz)
            target = parse_iso_to_dt(datetime, cfg["COUNTDOWN_TARGET_ISO"], cfg["COUNTDOWN_TZ"])
            remaining = target - now

            if remaining.total_seconds() <= 0:
                # Timer abgelaufen: Timer-Nachricht senden, falls vorhanden und noch nicht gesendet
                timer_message = cfg.get("COUNTDOWN_TIMER_MESSAGE")
                if timer_message and get_timer_message_sent and set_timer_message_sent:
                    message_sent = get_timer_message_sent()
                    if not message_sent:
                        try:
                            await channel.send(timer_message)
                            set_timer_message_sent(True)
                            logger.info("Timer-Nachricht wurde gesendet")
                        except Exception as exc:
                            logger.warning("Fehler beim Senden der Timer-Nachricht: %s", exc)
                await asyncio.sleep(600)
                continue

            send_now = False
            message = None

            if remaining <= timedelta(days=7):
                if remaining > timedelta(hours=24):
                    if now.hour == target.hour and now.minute == target.minute:
                        # Aufrunden: 6 Tage 23h 59min -> 7 Tage
                        days_left = math.ceil(remaining.total_seconds() / 86400)
                        message = f"Es sind noch {days_left} Tage bis zum Serverstart verbleibend."
                        send_now = True
                else:
                    midnight = target.replace(hour=0, minute=0, second=0, microsecond=0)
                    if now >= midnight:
                        # Aufrunden: 11h 59min -> 12h
                        hours_left = math.ceil(remaining.total_seconds() / 3600)
                        if now.hour == 0 and now.minute == 0:
                            message = f"Heute ist Start! Noch {hours_left} Stunden."
                            send_now = True
                        else:
                            checkpoints = [
                                timedelta(hours=12),
                                timedelta(hours=3),
                                timedelta(hours=2),
                                timedelta(hours=1),
                                timedelta(minutes=10),
                            ]
                            for cp in checkpoints:
                                if abs((remaining - cp).total_seconds()) < 60:
                                    if cp >= timedelta(hours=1):
                                        # Aufrunden für Stunden-Checkpoints
                                        hours_checkpoint = math.ceil(cp.total_seconds() / 3600)
                                        message = f"Nur noch {hours_checkpoint} Stunden bis zum Start!"
                                    else:
                                        message = "Nur noch 10 Minuten bis zum Start!"
                                    send_now = True
                                    break
            else:
                if now.weekday() == target.weekday() and now.hour == target.hour and now.minute == target.minute:
                    # Aufrunden auf ganze Wochen, um Off-by-One durch Sekunden/DST zu vermeiden
                    weeks_left = int(math.ceil(remaining.total_seconds() / (7 * 24 * 3600)))
                    if weeks_left < 1:
                        weeks_left = 1
                    message = f"Es sind noch {weeks_left} Wochen bis zum Serverstart verbleibend."
                    send_now = True

            if send_now and message:
                try:
                    # Vorherige Bot-Countdown-Nachricht löschen
                    last_id = get_last_msg_id()
                    if last_id:
                        try:
                            old = await channel.fetch_message(last_id)
                            if old and old.author == bot.user:
                                await old.delete()
                        except Exception:
                            pass
                    role_id = cfg.get("COUNTDOWN_ROLE_ID_INT")
                    if role_id:
                        try:
                            role = channel.guild.get_role(role_id) or await channel.guild.fetch_role(role_id)
                            # Rolle erwähnen via Mention-String, aber nicht bei manuellen mc!wielange, nur Auto-Scheduler
                            message_to_send = f"{role.mention} {message}"
                        except Exception:
                            # Fallback: Nutze Mention-String direkt per ID, falls Role-Fetch scheitert
                            message_to_send = f"<@&{int(role_id)}> {message}"
                    else:
                        message_to_send = message
                    sent = await channel.send(message_to_send)
                    set_last_msg_id(sent.id)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Countdown Fehler: %s", exc)
        await asyncio.sleep(60)

