# Minecraft ↔ Discord Chat Sync Bot

Synchronisiert Chat zwischen einem Minecraft-Server (RCON/Query) und einem Discord-Channel.
Dazu: GitHub-Benachrichtigungen, Auto-Cleanup und ein Countdown-Scheduler.

Läuft als Docker-Container auf VPS oder Homeserver. Konfiguration zur Laufzeit per
Slash-Command, persistiert in einem Volume.

## Schnellstart

```bash
cp env.template .env   # DISCORD_TOKEN eintragen, Rest nach Bedarf
docker compose up -d
```

Logs: `docker compose logs -f`

Änderungen am Code → `docker compose up -d --build`.

### Ohne Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.template .env
python bot.py
```

## Konfiguration

Zwei Ebenen, JSON gewinnt:

| Ebene | Was | Wo |
|---|---|---|
| ENV | Secrets, Verbindungsdaten, Startwerte | `.env` |
| JSON | alles per Slash-Command Gesetzte | `config.json` im Volume (`/data`) |

Ein per Slash-Command gesetzter Wert überschreibt die ENV-Variable dauerhaft. Der
`disable_*`-Command entfernt den Override wieder, danach greift wieder der ENV-Wert.
Secrets landen nie in `config.json`.

Pflicht ist nur `DISCORD_TOKEN`. Fehlen RCON/Query-Werte, startet der Bot trotzdem —
dann sind nur die reinen Discord-Features aktiv.

Alle Variablen stehen mit Kommentar in [env.template](env.template).

### Konfiguration sichern / ansehen

```bash
docker compose exec bot cat /data/config.json
```

Wenn du die Datei lieber direkt am Host bearbeiten willst, in
[docker-compose.yml](docker-compose.yml) auf den Bind-Mount umstellen (dort kommentiert).

## Discord-Bot einrichten

1. Discord Developer Portal → Application → Bot anlegen, Token kopieren → `DISCORD_TOKEN`.
2. Bei "Privileged Gateway Intents" **Message Content Intent** aktivieren.
3. OAuth2 → URL Generator: Scopes `bot` + `applications.commands`, Berechtigungen
   `Send Messages`, `Read Message History`, `Manage Messages` (für Auto-Cleanup).
4. Bot einladen, Channel-ID kopieren → `/set_server_channel`.

## Minecraft-Server vorbereiten

In `server.properties`:

```
enable-rcon=true
rcon.password=DEIN_PASSWORT
rcon.port=25575
enable-query=true
query.port=25565
```

Läuft der Minecraft-Server auf demselben Host, muss der Container an die RCON-Ports
kommen: entweder `SERVER_IP` auf `host.docker.internal` (Docker Desktop) bzw. die
Docker-Bridge-IP setzen, oder beide Container in dasselbe Docker-Netzwerk hängen und
den Service-Namen als `SERVER_IP` verwenden.

## Commands

Text-Commands (Prefix `mc!`, im Mirror-Channel zusätzlich `-`):

| Command | Wirkung |
|---|---|
| `mc!whitelistadd <name>` | Spieler zur Whitelist (nur im Mirror-Channel) |
| `mc!ping` | Online-Status und Spielerliste via Query |
| `mc!wielange` | Verbleibende Zeit bis zum Countdown-Ziel |
| `mc!ip` | Server-Adresse — erst ab Countdown-Ende |

Slash-Commands (brauchen "Manage Server", Countdown-Rolle "Administrator"):

| Command | Wirkung |
|---|---|
| `/set_server_channel` | Channel für die Minecraft-Brücke |
| `/set_githubupdate_channel` | Repo + Channel für Commit-/PR-Updates |
| `/disable_github` | GitHub-Updates aus |
| `/change_prefix` | Prefix für Text-Commands |
| `/set_cleanup` | Aufbewahrungsdauer und Intervall des Auto-Cleanups |
| `/set_countdown` | Countdown-Ziel (ISO) und Channel |
| `/disable_countdown` | Countdown aus |
| `/set_countdown_role` / `/disable_countdown_role` | Rolle, die bei Auto-Countdowns gepingt wird |
| `/set_timer_message` / `/clear_timer_message` | Nachricht beim Ablauf des Countdowns |
| `/set_server_address` | Öffentliche Join-Adresse für `mc!ip` und die Timer-Nachricht |
| `/show_config` | Aktuelle Konfiguration |

Alle Änderungen greifen sofort — kein Neustart nötig.

## Features im Detail

### Chat-Brücke
Nachrichten im Mirror-Channel gehen via RCON in den Minecraft-Chat (Prefix `[Discord]`).
Die Gegenrichtung (`[MC] …`) läuft über den `/mc`-Webhook, den der Bridge-Mod aus
[mod-jars/](mod-jars/) aufruft — dafür `MC_WEBHOOK_SECRET` setzen.

### GitHub-Updates
Zwei Modi:

- **Polling** (Standard): fragt die GitHub-API alle `GITHUB_POLL_INTERVAL_SECONDS` ab.
  Braucht keinen eingehenden Port — die richtige Wahl hinter NAT. Nur öffentliche Repos.
- **Webhook** (Echtzeit): `GITHUB_WEBHOOK_SECRET` setzen, Port 8080 in
  `docker-compose.yml` freigeben, im Repo unter Settings → Webhooks eintragen
  (Payload URL `https://deine-domain/github`, Content type `application/json`, dasselbe
  Secret). Sobald das Secret gesetzt ist, schaltet sich das Polling ab.

Gemeldet werden Push-Commits sowie PRs (opened, closed/merged, reopened,
ready_for_review, review_requested).

### Auto-Cleanup
Löscht Nachrichten im Mirror-Channel, die älter als `MESSAGE_CLEANUP_RETENTION_HOURS`
sind (Standard 48h, Lauf alle 60 Minuten). `retention_hours=0` schaltet ab.

### Countdown
- mehr als 7 Tage: wöchentlich am Ziel-Wochentag zur Zieluhrzeit
- weniger als 7 Tage: täglich zur Zieluhrzeit
- am Starttag: um 00:00 Uhr, dazu 12h / 3h / 2h / 1h / 10min vor dem Start
- bei Ablauf: die per `/set_timer_message` hinterlegte Nachricht, genau einmal

### Server-IP zum Start freigeben
`/set_server_address` hinterlegt die öffentliche Join-Adresse. Sie ist gesperrt,
solange der Countdown läuft:

- **vor Ablauf** — `mc!ip` antwortet nur mit der Restzeit
- **bei Ablauf** — die Adresse geht automatisch mit der Timer-Nachricht raus
- **danach** — `mc!ip` gibt sie jederzeit aus

In der Timer-Nachricht bestimmt `{ip}` die Position der Adresse:

```
/set_timer_message message: @everyone Der Server ist offen! Join: {ip}
```

Ohne `{ip}` wird sie als eigene Zeile angehängt. Ist kein Countdown gesetzt, ist
`mc!ip` sofort frei.

## Betrieb

### VPS mit öffentlicher IP
Port-Mapping in `docker-compose.yml` einkommentieren. Den Webhook-Port nicht direkt
ins Internet hängen — Reverse-Proxy (Caddy, nginx, Traefik) mit TLS davor.
Health-Check: `GET /healthz`.

### Homeserver hinter NAT
Port-Mapping auskommentiert lassen, `GITHUB_WEBHOOK_SECRET` leer → Polling.
Der Bot baut nur ausgehende Verbindungen auf, es muss nichts weitergeleitet werden.
Der MC-Webhook funktioniert weiterhin, wenn der Minecraft-Server im selben LAN läuft.

## Projektstruktur

| Datei | Aufgabe |
|---|---|
| [bot.py](bot.py) | Bootstrap: Client, Events, Task-Start |
| [app/config.py](app/config.py) | Config-Auflösung (JSON > ENV > Default) und Persistenz |
| [app/tasks.py](app/tasks.py) | GitHub-Polling, Cleanup, Countdown-Scheduler |
| [app/commands.py](app/commands.py) | Text- und Slash-Commands |
| [app/webhooks.py](app/webhooks.py) | HTTP-Endpunkte `/healthz`, `/github`, `/mc` |
| [test_bot.py](test_bot.py) | Selbst-Check: `python test_bot.py` |
| [mod-jars/](mod-jars/) | Ablage für den serverseitigen Bridge-Mod |

## Fehlerbehebung

| Symptom | Ursache |
|---|---|
| Bot startet nicht | `docker compose logs bot` — fehlende ENV-Vars werden explizit gemeldet |
| Config nach Neustart weg | Volume fehlt oder `CONFIG_PATH` zeigt nicht nach `/data` |
| "Server ist offline" | RCON/Query-Ports vom Container aus nicht erreichbar |
| Slash-Commands fehlen | Bot ohne `applications.commands`-Scope eingeladen |
| Nachrichten kommen nicht an | Message Content Intent nicht aktiviert |
