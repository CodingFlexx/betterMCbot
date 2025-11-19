# Mod-JAR-Ablage

Dieser Ordner ist der zentrale Ablageort für alle serverseitigen Mod-Builds des Bots. Die eigentlichen `.jar`-Dateien werden **nicht** versioniert, sondern hier nach der manuellen Erstellung abgelegt.

## Aktueller Planungsstand
- Erste zu bauende Version: `1.12.2`
- Empfohlenes Namensschema: `better-mc-bridge-<mc-version>.jar`

## Manuelles Vorgehen
1. Mod-Quellcode im entsprechenden Mod-Projekt bauen (z. B. mittels Gradle).
2. Das erzeugte JAR in diesen Ordner kopieren.
3. Prüfen, dass der Dateiname eindeutig die Minecraft-Version enthält.
4. Das JAR nicht commiten – dank `.gitignore` bleibt es lokal.

## Hinweise
- Du kannst zusätzliche Unterordner verwenden (z. B. `legacy/`, `fabric/`, `forge/`), die README muss dann ggf. aktualisiert werden.
- Dieser Ordner kann zur Distribution in Deployments eingebunden werden, falls der Mod direkt auf demselben Server liegt.
