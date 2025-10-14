import os
import json
import logging
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger("betterMCbot.settings")

# Env
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_IP = os.getenv("SERVER_IP")
RCON_PORT = os.getenv("RCON_PORT")
RCON_PASSWORD = os.getenv("RCON_PASSWORD")
QUERY_PORT = os.getenv("QUERY_PORT")
CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_UPDATES_CHANNEL_ID = os.getenv("GITHUB_UPDATES_CHANNEL_ID")
GITHUB_POLL_INTERVAL_SECONDS = os.getenv("GITHUB_POLL_INTERVAL_SECONDS", "120")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "bot_config")
MESSAGE_CLEANUP_RETENTION_HOURS = os.getenv("MESSAGE_CLEANUP_RETENTION_HOURS", "48")
MESSAGE_CLEANUP_INTERVAL_MINUTES = os.getenv("MESSAGE_CLEANUP_INTERVAL_MINUTES", "60")
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

def _parse_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None

def load_json_file(path: str):
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Konfigurationsdatei konnte nicht geladen werden: %s", exc)
        return {}

def save_json_file(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Konfigurationsdatei konnte nicht gespeichert werden: %s", exc)

_supabase = None
def init_supabase():
    global _supabase
    if _supabase is not None:
        return _supabase
    if not SUPABASE_URL:
        return None
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not key:
        return None
    try:
        _supabase = create_client(SUPABASE_URL, key)
        logger.info("Supabase-Client initialisiert")
        return _supabase
    except Exception as exc:
        logger.warning("Supabase-Init fehlgeschlagen: %s", exc)
        return None

def load_config():
    sb = init_supabase()
    if sb:
        try:
            res = sb.table(SUPABASE_TABLE).select("config").eq("id", 1).limit(1).execute()
            rows = getattr(res, "data", []) or []
            if rows:
                cfg = rows[0].get("config")
                if isinstance(cfg, dict):
                    # Wenn Supabase leer ist, aber lokale Datei Werte hat → migrieren
                    if not cfg:
                        file_cfg = load_json_file(CONFIG_PATH)
                        if file_cfg:
                            try:
                                sb.table(SUPABASE_TABLE).upsert({"id": 1, "config": file_cfg}).execute()
                                return file_cfg
                            except Exception:
                                pass
                    return cfg
        except Exception as exc:
            logger.warning("Supabase Load fehlgeschlagen: %s", exc)
    return load_json_file(CONFIG_PATH)

def save_config(data: dict):
    sb = init_supabase()
    if sb:
        try:
            sb.table(SUPABASE_TABLE).upsert({"id": 1, "config": data}).execute()
            return
        except Exception as exc:
            logger.warning("Supabase Save fehlgeschlagen: %s", exc)
    save_json_file(CONFIG_PATH, data)

