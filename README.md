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
  - `-whitelist <name>`: Fügt Spieler zur Whitelist hinzu
  - `-ping`: Zeigt Online-Status und Spielerliste via Query

### Betrieb ohne Minecraft-Server (degradierter Modus)
- Der Bot startet auch, wenn keine RCON/Query-Parameter gesetzt sind.
- Dann sind nur reine Discord-Features aktiv; Brücke/Whitelist/Ping reagieren mit Hinweisen oder sind deaktiviert.

## Fehlerbehebung
- Prüfe Railway-Logs, wenn der Bot nicht startet (fehlende Env-Vars werden explizit gemeldet).
- Stelle sicher, dass die Ports/Firewall für RCON und Query erreichbar sind.
- Prüfe Bot-Rechte und aktivierte Intents in Discord.
