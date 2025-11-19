# Minecraft ↔ Discord Chat Sync Bot

Synchronisiert Chat zwischen einem Minecraft-Server (RCON/Query) und einem Discord-Channel. Enthält einfache Commands für Whitelist und Server-Status.

## Voraussetzungen
- Python 3.10–3.12
- Zugriff auf den Minecraft-Server mit aktivem RCON und Query
- Discord Application mit Bot-Token

## Konfiguration
Lege eine `.env` Datei an (oder setze Vars in Railway) gemäß `env.template`:

```
DISCORD_TOKEN="..."
SERVER_IP="1.2.3.4"
RCON_PORT="25575"
RCON_PASSWORD="secret"
QUERY_PORT="25565"
CHAT_CHANNEL_ID="123456789012345678"
```

Hinweise:
- `CHAT_CHANNEL_ID` ist die numerische ID des Discord-Channels, der synchronisiert werden soll.
- Stelle sicher, dass RCON in `server.properties` aktiviert ist und das Passwort korrekt gesetzt ist.

## Lokal starten
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Deployment auf Railway
Dieses Repo enthält `Procfile` und `railway.toml`. Schritte:

1) Neues Projekt anlegen oder dieses Repo deployen.
2) Service erstellen (Python wird via Nixpacks erkannt).
3) Environment Variables im Railway-Dashboard setzen:
   - `DISCORD_TOKEN`, `SERVER_IP`, `RCON_PORT`, `RCON_PASSWORD`, `QUERY_PORT`, `CHAT_CHANNEL_ID`.
4) Deploy starten. Der Prozess nutzt `worker: python bot.py`.

### Discord-Bot erstellen und mit Server verbinden
1) Besuche das Discord Developer Portal und erstelle eine Application. Erstelle unter "Bot" einen Bot und kopiere das Token → `DISCORD_TOKEN`.
2) Unter "Privileged Gateway Intents" aktiviere "Message Content Intent" (wird benötigt).
3) Unter "OAuth2 → URL Generator": Scopes `bot`, Berechtigungen mindestens `Send Messages`, `Read Message History`.
4) Lade den Bot auf deinen Server ein (verwende die generierte URL) und notiere die Ziel-Channel-ID → `CHAT_CHANNEL_ID`.

### Minecraft-Server vorbereiten
In der `server.properties`:
- `enable-rcon=true`
- `rcon.password=DEIN_PASSWORT`
- `rcon.port=25575` (oder Wunsch-Port)
- Query aktivieren (je nach Setup/Hoster): `enable-query=true`, `query.port=25565`

## Nutzung
- Nachrichten im angegebenen Discord-Channel werden via RCON in den Minecraft-Chat gespiegelt (Prefix `[Discord]`).
- Commands im Discord:
  - `-whitelistadd <name>`: Fügt Spieler zur Whitelist hinzu (nur im Mirror-Channel)
  - `mc!ping`: Zeigt Online-Status und Spielerliste via Query
  - `mc!wielange`: Zeigt verbleibende Zeit bis zum Countdown-Ziel

### Betrieb ohne Minecraft-Server (degradierter Modus)
- Der Bot startet auch, wenn keine RCON/Query-Parameter gesetzt sind.
- Dann sind nur reine Discord-Features aktiv; Brücke/Whitelist/Ping reagieren mit Hinweisen oder sind deaktiviert.

## GitHub Commit-Updates
Der Bot kann neue Commits eines Repos periodisch abfragen und in einen Channel posten.

Umgebungsvariablen:
```
GITHUB_REPO="owner/repo"
GITHUB_UPDATES_CHANNEL_ID="123456789012345678"
GITHUB_POLL_INTERVAL_SECONDS="120"  # optional
```

Hinweise:
- Standard: Polling des öffentlichen GitHub-API-Endpoints (ohne Token). Für private Repos bräuchte man ein Token und angepasste Auth-Header.
- Beim ersten Start wird nur der neueste Commit als Referenz gemerkt; neue Commits seitdem werden gepostet.

### Webhook (Echtzeit)
Statt Polling kannst du Webhooks aktivieren:

1) Setze in Railway-Variables `GITHUB_WEBHOOK_SECRET` (beliebiger geheimer String).
2) Stelle sicher, dass der Service als Web läuft (Procfile nutzt `web: python bot.py`).
3) GitHub-Repo → Settings → Webhooks → Add webhook:
   - Payload URL: `https://<deine-railway-domain>/github`
   - Content type: `application/json`
   - Secret: derselbe Wert wie `GITHUB_WEBHOOK_SECRET`
   - Events: "Just the push event" (oder was du brauchst)
4) Wenn `GITHUB_WEBHOOK_SECRET` gesetzt ist, wird Polling automatisch deaktiviert.

## Konfiguration via Slash-Commands
Die wichtigsten Einstellungen lassen sich jetzt direkt in Discord setzen (nur Nutzer mit "Manage Server"):

- `/set_server_channel channel:<#channel>`: Setzt den Discord-Channel für die Minecraft-Brücke.
- `/set_githubupdate_channel repo:owner/repo channel:<#channel> [poll_interval_seconds:120]`: Aktiviert GitHub-Updates für ein Repo in einem Channel.
- `/change_prefix prefix:<text>`: Ändert das Prefix für klassische Text-Commands (Standard `mc!`).
- `/set_cleanup [retention_hours:<int>] [interval_minutes:<int>]`: Setzt Auto-Cleanup (Standard 48h/60m).
- `/set_countdown target_iso:<YYYY-MM-DDTHH:MM> channel:<#channel> [timezone_name:Europe/Berlin]`: Aktiviert den Countdown.
- `/disable_countdown`: Deaktiviert den Countdown.
- `/disable_github`: Deaktiviert die GitHub-Updates.
- `/show_config`: Zeigt die aktuelle Konfiguration.

Persistenz: Die Einstellungen werden in `config.json` im Projektverzeichnis gespeichert (überschreiben Environment-Werte zur Laufzeit). Bei Neu-Deploys ohne Persistenz muss neu gesetzt werden.

## Optionale Persistenz mit Supabase
Für dauerhafte Speicherung über Deploys hinweg kannst du Supabase nutzen.

## Auto-Cleanup des Mirror-Channels
- Standardmäßig löscht der Bot Nachrichten im Mirror-Channel, die älter als 48 Stunden sind (Job läuft alle 60 Minuten).
- Konfiguration via Slash-Command oder ENV: `MESSAGE_CLEANUP_RETENTION_HOURS`, `MESSAGE_CLEANUP_INTERVAL_MINUTES`.

## Countdown-Feature
- Setze Datum/Uhrzeit (ISO) und Channel via `/set_countdown`.
- Verhalten:
  - Mehr als 7 Tage bis zum Start: Wöchentliche Nachricht am gleichen Wochentag/Uhrzeit wie das Ziel.
  - Weniger als 7 Tage: Tägliche Nachricht zur Zieluhrzeit.
  - Am Starttag: 00:00 und 12:00 Uhr verbleibende Stunden; zusätzlich 3h/2h/1h/10min vorher.
- Optionale TZ per ENV `TIMEZONE` (Standard `Europe/Berlin`).
 - Persistenz: Countdown-Channel, Ziel und letzte Bot-Message-ID werden in Supabase (oder `config.json`) gespeichert. Beim Erststart ohne Supabase-Inhalt werden vorhandene Datei-Werte automatisch migriert.

## Projektstruktur (aufgeräumt)
Die wichtigsten Module und deren Aufgaben:

- `bot.py`: Bootstrap. Startet den Bot, lädt Konfig, verdrahtet Events/Tasks/Commands.
- `app/settings.py`: Konfiguration & Persistenz
  - Lädt ENV-Variablen
  - Speichert/Lädt Konfig via Supabase (Fallback: `config.json`)
  - `load_config()` / `save_config()` als zentrale API
- `app/tasks.py`: Hintergrundprozesse
  - GitHub-Updates (Polling/Webhook-Server)
  - Auto-Cleanup des Mirror-Channels
  - Countdown-Scheduler
- `app/commands.py`: Befehle
  - Text-Commands (`-whitelistadd`, `mc!ping`, `mc!wielange`, …)
  - Slash-Commands (`/set_server_channel`, `/set_githubupdate_channel`, `/change_prefix`, `/set_cleanup`, `/set_countdown`, …)

Weitere Dateien:
- `env.template`: Vorlage der ENV-Variablen
- `requirements.txt`: Python-Abhängigkeiten
- `Procfile`: Start als Web-Prozess (Webhook-Unterstützung)
- `railway.toml`: Railway-Service-Konfiguration
- `mod-jars/`: Ablageordner für lokal gebaute Mod-JARs (z. B. Version `1.12.2`)
ENV-Variablen (mindestens):
```
SUPABASE_URL
SUPABASE_ANON_KEY  # oder SUPABASE_SERVICE_ROLE_KEY
SUPABASE_TABLE=bot_config
```

Schema (einfachste Variante, eine Zeile):
```sql
create table if not exists bot_config (
  id int primary key default 1,
  config jsonb
);
insert into bot_config (id, config) values (1, '{}'::jsonb)
on conflict (id) do nothing;
```

Hinweise:
- Der Bot macht ein Upsert auf `id = 1` und speichert die komplette Konfiguration als JSON.
- Ohne Supabase fällt der Bot automatisch auf Dateispeicherung (`config.json`) zurück.

## Serverseitiger Mod-Build (Vorbereitung)

- Der Ordner `mod-jars/` ist bereits angelegt und wird versioniert, echte `.jar`-Artefakte sind per `.gitignore` ausgeschlossen.
- Erste Zielversion ist `1.12.2`. Benenne das spätere Artefakt z. B. `better-mc-bridge-1.12.2.jar` und lege es in `mod-jars/` ab.
- Empfohlenes manuelles Vorgehen:
  1. Mod im jeweiligen Build-System (z. B. Gradle) erzeugen.
  2. Datei nach `mod-jars/` kopieren; Unterordner können frei gewählt werden.
  3. Repository bleibt sauber, weil nur die README in diesem Ordner committed wird.
- Dokumentation und weitere Hinweise findest du auch in `mod-jars/README.md`.

## Fehlerbehebung
- Prüfe Railway-Logs, wenn der Bot nicht startet (fehlende Env-Vars werden explizit gemeldet).
- Stelle sicher, dass die Ports/Firewall für RCON und Query erreichbar sind.
- Prüfe Bot-Rechte und aktivierte Intents in Discord.
