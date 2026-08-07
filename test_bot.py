"""Selbst-Check: python test_bot.py

Deckt ab, was still kaputtgehen kann: Config-Precedence (JSON > ENV > Default),
Persistenz und die Countdown-Zeitfenster.
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Config
from app.tasks import (
    countdown_message,
    countdown_reached,
    format_precise_delta,
    format_time_delta,
    parse_iso_to_aware_dt,
    render_timer_message,
)

TZ = ZoneInfo("Europe/Berlin")


def test_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"

        os.environ.pop("CHAT_CHANNEL_ID", None)
        cfg = Config(path)
        assert cfg.command_prefix == "mc!", "Default"
        assert cfg.chat_channel_id is None
        assert cfg.message_cleanup_retention_hours == 48

        os.environ["CHAT_CHANNEL_ID"] = "111"
        assert cfg.chat_channel_id == 111, "ENV schlägt Default"

        cfg.set(chat_channel_id=222)
        assert cfg.chat_channel_id == 222, "JSON schlägt ENV"
        assert Config(path).chat_channel_id == 222, "überlebt Neustart"

        cfg.set(chat_channel_id=None)
        assert cfg.chat_channel_id == 111, "None entfernt Override, ENV greift wieder"
        del os.environ["CHAT_CHANNEL_ID"]

        # Secrets dürfen nie in der Datei landen
        assert "discord_token" not in path.read_text()
        try:
            cfg.set(discord_token="leak")
            raise AssertionError("unbekannte Keys müssen abgelehnt werden")
        except KeyError:
            pass

        cfg.set(countdown_timer_message_sent=True)
        assert Config(path).countdown_timer_message_sent is True


def test_format_time_delta():
    assert format_time_delta(timedelta(days=6, hours=23, minutes=59)) == "7 Tage"
    assert format_time_delta(timedelta(hours=11, minutes=59)) == "12 Std"
    assert format_time_delta(timedelta(minutes=59, seconds=59)) == "59 Min"
    assert format_time_delta(timedelta(seconds=-5)) == "0 Min"


def test_parse_iso():
    assert parse_iso_to_aware_dt("2025-12-31T17:00", "Europe/Berlin") == datetime(2025, 12, 31, 17, 0, tzinfo=TZ)
    assert parse_iso_to_aware_dt("kaputt", "Europe/Berlin").tzinfo == TZ  # Fallback statt Crash


def test_countdown_message():
    target = datetime(2025, 12, 31, 17, 0, tzinfo=TZ)  # Mittwoch

    def at(**kwargs):
        now = target - timedelta(**kwargs)
        return countdown_message(now, target, target - now)

    # > 7 Tage: nur am gleichen Wochentag zur Zieluhrzeit
    assert at(weeks=3) == "Es sind noch 3 Wochen bis zum Serverstart verbleibend."
    assert at(weeks=3, hours=1) is None
    assert at(weeks=3, days=1) is None

    # <= 7 Tage: täglich zur Zieluhrzeit, aufgerundet
    assert at(days=3) == "Es sind noch 3 Tage bis zum Serverstart verbleibend."
    assert at(days=3, minutes=1) is None

    # Starttag: nur 00:00 als feste Uhrzeit
    assert at(hours=17) == "Heute ist Start! Noch 17 Stunden."
    assert at(hours=5) is None, "12:00 ist kein Fixpunkt — nur der 12h-Checkpoint zaehlt"
    assert at(hours=13) is None, "vor Mitternacht des Starttags noch keine Stundenmeldung"

    # Checkpoints
    assert at(hours=12) == "Nur noch 12 Stunden bis zum Start!"
    assert at(hours=3) == "Nur noch 3 Stunden bis zum Start!"
    assert at(hours=1) == "Nur noch 1 Stunden bis zum Start!"
    assert at(minutes=10) == "Nur noch 10 Minuten bis zum Start!"
    assert at(minutes=30) is None

    # Ziel mit krummer Minute: Checkpoints müssen trotzdem greifen (war vorher kaputt)
    odd = datetime(2025, 12, 31, 17, 30, tzinfo=TZ)
    now = odd - timedelta(minutes=10)
    assert countdown_message(now, odd, odd - now) == "Nur noch 10 Minuten bis zum Start!"
    midnight = odd.replace(hour=0, minute=0)
    assert countdown_message(midnight, odd, odd - midnight) == "Heute ist Start! Noch 18 Stunden."


def test_format_precise_delta():
    assert format_precise_delta(timedelta(days=2, hours=5, minutes=13)) == "2 Tage, 5 Stunden, 13 Minuten"
    assert format_precise_delta(timedelta(minutes=90)) == "0 Tage, 1 Stunden, 30 Minuten"
    assert format_precise_delta(timedelta(seconds=-5)) == "0 Tage, 0 Stunden, 0 Minuten"


def test_countdown_checkpoints_within_24h():
    # Ziel 18:00 — die Checkpoints haengen am Ziel, nicht an der Uhrzeit
    target = datetime(2026, 9, 1, 18, 0, tzinfo=ZoneInfo("Europe/Berlin"))

    def at(hour, minute):
        now = datetime(2026, 9, 1, hour, minute, tzinfo=ZoneInfo("Europe/Berlin"))
        return countdown_message(now, target, target - now)

    assert at(0, 0) == "Heute ist Start! Noch 18 Stunden."
    assert at(6, 0) == "Nur noch 12 Stunden bis zum Start!", "12h relativ zum Ziel, nicht fix um 12:00"
    assert at(15, 0) == "Nur noch 3 Stunden bis zum Start!"
    assert at(17, 0) == "Nur noch 1 Stunden bis zum Start!"
    assert at(17, 50) == "Nur noch 10 Minuten bis zum Start!"
    # Der Bug aus 71bd1c3: Checkpoints feuerten nur zur Ziel-Minute
    assert at(9, 17) is None, "kein Checkpoint -> keine Nachricht"
    assert at(12, 0) is None, "12:00 ist kein Fixpunkt mehr"


def test_render_timer_message():
    assert render_timer_message("Los geht's!", "mc.example.com") == "Los geht's!\nIP: `mc.example.com`"
    assert render_timer_message("Join: {ip} — viel Spaß", "1.2.3.4") == "Join: 1.2.3.4 — viel Spaß"
    assert render_timer_message("Los geht's!", None) == "Los geht's!", "ohne Adresse unverändert"
    assert render_timer_message("Server: {ip}", None) == "Server:", "Platzhalter darf nicht sichtbar bleiben"


def test_countdown_reached():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.pop("COUNTDOWN_TARGET_ISO", None)
        cfg = Config(Path(tmp) / "config.json")
        assert countdown_reached(cfg) is True, "ohne Countdown keine Sperre"

        cfg.set(countdown_target_iso="2020-01-01T12:00")
        assert countdown_reached(cfg) is True, "Vergangenheit = erreicht"

        cfg.set(countdown_target_iso="2999-01-01T12:00")
        assert countdown_reached(cfg) is False, "Zukunft = gesperrt"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("alle Checks bestanden")
