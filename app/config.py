"""Zentrale Konfiguration.

Zwei Ebenen:
  - ENV: Secrets und Verbindungsdaten. Nur beim Start gelesen, nie geschrieben.
  - JSON: alles, was per Slash-Command zur Laufzeit änderbar ist. Überschreibt ENV.

Alle Werte werden bei jedem Zugriff frisch aufgelöst, damit Änderungen sofort
in laufenden Tasks greifen (kein Neustart nötig).
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("betterMCbot.config")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _str(value):
    return str(value).strip() or None


# key -> (env_var, parser, default)
SCHEMA = {
    "chat_channel_id": ("CHAT_CHANNEL_ID", _int, None),
    "command_prefix": (None, _str, "mc!"),
    "github_repo": ("GITHUB_REPO", _str, None),
    "github_updates_channel_id": ("GITHUB_UPDATES_CHANNEL_ID", _int, None),
    "github_poll_interval_seconds": ("GITHUB_POLL_INTERVAL_SECONDS", _int, 120),
    "message_cleanup_retention_hours": ("MESSAGE_CLEANUP_RETENTION_HOURS", _int, 48),
    "message_cleanup_interval_minutes": ("MESSAGE_CLEANUP_INTERVAL_MINUTES", _int, 60),
    "countdown_channel_id": (None, _int, None),
    "countdown_target_iso": ("COUNTDOWN_TARGET_ISO", _str, None),
    "countdown_timezone": ("TIMEZONE", _str, "Europe/Berlin"),
    "countdown_role_id": (None, _int, None),
    "countdown_last_message_id": (None, _int, None),
    "countdown_last_auto_message_id": (None, _int, None),
    "countdown_last_trigger_id": (None, _int, None),
    "countdown_timer_message": (None, _str, None),
    "countdown_timer_message_sent": (None, _bool, False),
    # Öffentliche Join-Adresse. Nicht SERVER_IP — das ist der RCON-Host und kann
    # eine interne Adresse sein.
    "server_address": ("SERVER_ADDRESS", _str, None),
}


class Config:
    def __init__(self, path=None):
        self._overrides = {}
        self.path = Path(path or os.getenv("CONFIG_PATH", "config.json"))

        # ENV-only, wird nie in die JSON-Datei geschrieben
        self.discord_token = os.getenv("DISCORD_TOKEN")
        self.server_ip = os.getenv("SERVER_IP")
        self.rcon_port = _int(os.getenv("RCON_PORT"))
        self.rcon_password = os.getenv("RCON_PASSWORD")
        self.query_port = _int(os.getenv("QUERY_PORT"))
        self.github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
        self.mc_webhook_secret = os.getenv("MC_WEBHOOK_SECRET")
        self.webhook_port = _int(os.getenv("WEBHOOK_PORT")) or 8080

        self.reload()

    def reload(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Konfiguration %s nicht lesbar: %s", self.path, exc)
            return
        if isinstance(data, dict):
            self._overrides = {k: v for k, v in data.items() if k in SCHEMA}

    def __getattr__(self, name):
        # Nur für SCHEMA-Keys; verhindert Rekursion über self._overrides
        if name not in SCHEMA:
            raise AttributeError(name)
        env_var, parse, default = SCHEMA[name]
        raw = self._overrides.get(name)
        if raw is None and env_var:
            raw = os.getenv(env_var)
        if raw is None or raw == "":
            return default
        value = parse(raw)
        return default if value is None else value

    def set(self, **values):
        """Setzt Werte und schreibt sie in die JSON-Datei. None löscht einen Key."""
        for key, value in values.items():
            if key not in SCHEMA:
                raise KeyError(key)
            if value is None:
                self._overrides.pop(key, None)
            else:
                self._overrides[key] = value
        self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._overrides, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except Exception as exc:
            logger.warning("Konfiguration konnte nicht gespeichert werden: %s", exc)

    # Feature-Flags
    @property
    def has_rcon(self):
        return bool(self.server_ip and self.rcon_password and self.rcon_port)

    @property
    def has_query(self):
        return bool(self.server_ip and self.query_port)

    @property
    def has_bridge(self):
        return bool(self.has_rcon and self.chat_channel_id)

    @property
    def has_github(self):
        return bool(self.github_repo and self.github_updates_channel_id)

    @property
    def webhooks_enabled(self):
        return bool(self.github_webhook_secret or self.mc_webhook_secret)

    def as_dict(self):
        """Config zum Anzeigen — ohne Secrets."""
        data = {key: getattr(self, key) for key in SCHEMA}
        data["features"] = {
            "bridge": self.has_bridge,
            "rcon": self.has_rcon,
            "query": self.has_query,
            "github": self.has_github,
            "webhooks": self.webhooks_enabled,
        }
        return data
