"""Plain-text server information and a bounded asynchronous Query reader."""
import asyncio
from datetime import datetime
import time
from zoneinfo import ZoneInfo

import discord
from mcipc.query import Client as QueryClient

from app.tasks import format_precise_delta, parse_iso_to_aware_dt


def countdown_text(cfg, now=None):
    if not cfg.countdown_target_iso:
        return "Kein Countdown-Ziel gesetzt."
    try:
        zone = ZoneInfo(cfg.countdown_timezone)
        now = datetime.now(zone) if now is None else now.astimezone(zone)
        target = parse_iso_to_aware_dt(cfg.countdown_target_iso, cfg.countdown_timezone)
    except (ValueError, TypeError, KeyError):
        return "Der Countdown ist derzeit ungültig konfiguriert."
    if target <= now:
        return "Der Countdown ist abgelaufen."
    return f"Verbleibende Zeit: {format_precise_delta(target - now)}\nZiel: {target:%d.%m.%Y %H:%M %Z}"


def countdown_gate_text(cfg, now=None):
    """Block public server information until the configured start time."""
    if cfg.countdown_target_iso:
        try:
            zone = ZoneInfo(cfg.countdown_timezone)
            now = datetime.now(zone) if now is None else now.astimezone(zone)
            target = parse_iso_to_aware_dt(cfg.countdown_target_iso, cfg.countdown_timezone)
        except (ValueError, TypeError, KeyError):
            return "Serverstatus und Adresse werden nach einem gültigen Countdown freigegeben."
        if target > now:
            return f"Dieser Command ist erst ab dem {target:%d.%m.%Y} um {target:%H:%M} Uhr ({target:%Z}) verfügbar."
    return None


def address_text(cfg, now=None):
    blocked = countdown_gate_text(cfg, now)
    if blocked:
        return blocked
    if not cfg.server_address:
        return "Keine Server-Adresse hinterlegt."
    # Only the intentionally configured public join address, never SERVER_IP/RCON.
    address = discord.utils.escape_mentions(str(cfg.server_address)).replace("`", "\\`")
    return f"Server-Adresse: `{address}`"[:1900]


def help_text(cfg):
    prefix = discord.utils.escape_mentions(discord.utils.escape_markdown(str(cfg.command_prefix)))
    return ("**Minecraft-Servermenü**\n"
            "Serverinformationen und Hilfe als Text.\n"
            f"Textbefehle: `{prefix}menu`, `{prefix}ping`, `{prefix}wielange`, `{prefix}ip`.\n"
            "Der Status ist gegebenenfalls auf den Minecraft-Mirror-Channel beschränkt.\n"
            "In Minecraft: `/ctf` für Flagbound-Befehle und `/ctf vote list` für Termine.\n"
            "Dieses Menü verändert weder Server, Whitelist noch Bot-Konfiguration.")


class StatusReader:
    """Short cache plus serialized UDP query; command bursts do not block Discord."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cached_key = None
        self.cached_text = None
        self.valid_until = 0.0

    def _query(self, host, port):
        with QueryClient(host, port, timeout=3) as client:
            return client.stats(full=True)

    async def read(self, cfg, channel_id):
        # Revalidate the same channel restriction as the existing ping command on
        # every request, including after a configuration change.
        if cfg.chat_channel_id and cfg.chat_channel_id != channel_id:
            return "Serverstatus bitte im konfigurierten Minecraft-Mirror-Channel öffnen."
        blocked = countdown_gate_text(cfg)
        if blocked:
            return blocked
        if not cfg.has_query:
            return "Minecraft-Query ist nicht konfiguriert."
        key = (cfg.server_ip, cfg.query_port)
        async with self.lock:
            if self.cached_key == key and self.cached_text is not None and time.monotonic() < self.valid_until:
                return self.cached_text
            try:
                stats = await asyncio.wait_for(asyncio.to_thread(self._query, *key), timeout=5)
                # mcipc 2.4 FullStats is a NamedTuple, not a string-keyed dict.
                field = lambda name, default: stats.get(name, default) if isinstance(stats, dict) else getattr(stats, name, default)
                players = list(field("players", []))
                text = f"Server online: **{field('num_players', len(players))}/{field('max_players', '?')}** Spieler"
                if players:
                    names = [discord.utils.escape_mentions(discord.utils.escape_markdown(str(name)[:64])) for name in players[:40]]
                    text += "\n" + ", ".join(names)
                    if len(players) > 40:
                        text += f"\n… und {len(players) - 40} weitere."
                text = text[:1900]
            except (OSError, ValueError, TypeError, asyncio.TimeoutError):
                text = "Serverstatus momentan nicht erreichbar."
            except Exception:
                text = "Serverstatus momentan nicht verfügbar."
            self.cached_key, self.cached_text = key, text
            self.valid_until = time.monotonic() + 5
            return text


STATUS = StatusReader()
