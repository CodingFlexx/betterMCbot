import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

def register_text_commands(bot: commands.Bot, deps):
    mcipc_Client = deps["mcipc_Client"]
    QueryClient = deps["QueryClient"]

    @bot.command(name='whitelistadd')
    async def whitelistadd(ctx, *, arg):
        if deps["CHAT_CHANNEL_ID_INT"] and ctx.channel.id != deps["CHAT_CHANNEL_ID_INT"]:
            return
        name = arg
        try:
            if not deps["HAS_RCON"]:
                await ctx.send("Minecraft-RCON ist nicht konfiguriert.")
                return
            with mcipc_Client(deps["SERVER_IP"], deps["RCON_PORT_INT"], passwd=deps["RCON_PASSWORD"]) as client:
                whitelist = client.whitelist
                whitelist.add(name)
                await ctx.send("Spieler " + name + " wurde zur Whitelist hinzugefügt")
        except Exception:
            await ctx.send("Server nicht erreichbar")

    @bot.command(name='ping')
    async def ping(ctx):
        if deps["CHAT_CHANNEL_ID_INT"] and ctx.channel.id != deps["CHAT_CHANNEL_ID_INT"]:
            return
        try:
            if not deps["HAS_QUERY"]:
                await ctx.send("Minecraft-Query ist nicht konfiguriert.")
                return
            with QueryClient(deps["SERVER_IP"], deps["QUERY_PORT_INT"]) as client:
                status = client.stats(full=True)
                ans = "Server ist online mit " + str(status['num_players']) + "/" + str(
                    status['max_players']) + " Spielern:"
                for player in status['players']:
                    ans += "\n\t" + player
                await ctx.send(ans)
        except Exception:
            await ctx.send("Server ist offline")

    @bot.command(name='wielange', aliases=['countdown'])
    async def wielange(ctx):
        if not deps["COUNTDOWN_TARGET_ISO"]:
            await ctx.send("Kein Countdown-Ziel gesetzt.")
            return
        tz = deps["ZoneInfo"](deps["COUNTDOWN_TZ"])
        now = deps["datetime"].now(tz)
        target = deps["parse_iso_to_dt"](deps["datetime"], deps["COUNTDOWN_TARGET_ISO"], deps["COUNTDOWN_TZ"])
        remaining = target - now
        if remaining.total_seconds() <= 0:
            await ctx.send("Der Zeitpunkt ist bereits erreicht.")
            return
        # Vorherige Bot-Countdown-Nachricht + vorherige Trigger-Nachricht löschen
        get_last = deps.get("get_last_msg_id")
        set_last = deps.get("set_last_msg_id")
        get_trig = deps.get("get_last_trigger_id")
        set_trig = deps.get("set_last_trigger_id")
        if get_last:
            try:
                last_id = get_last()
                if last_id:
                    try:
                        old = await ctx.channel.fetch_message(last_id)
                        if old and old.author == bot.user:
                            await old.delete()
                    except Exception:
                        pass
            except Exception:
                pass
        if get_trig:
            try:
                trig_id = get_trig()
                if trig_id and trig_id != ctx.message.id:
                    try:
                        old_trig = await ctx.channel.fetch_message(trig_id)
                        if old_trig:
                            await old_trig.delete()
                    except Exception:
                        pass
            except Exception:
                pass
        # Aktuelle Nachricht stehen lassen, neue Antwort senden und IDs speichern
        sent = await ctx.send("Verbleibende Zeit: " + deps["fmt_td"](remaining))
        if set_last:
            set_last(sent.id)
        if set_trig:
            set_trig(ctx.message.id)


def register_slash_commands(bot: commands.Bot, deps):
    load_config = deps["load_config"]
    save_config = deps["save_config"]

    @bot.tree.command(name="set_server_channel", description="Setzt den Discord-Channel für die Minecraft-Brücke")
    @app_commands.describe(channel="Ziel-Channel für Brücke")
    @app_commands.default_permissions(manage_guild=True)
    async def set_server_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        data = load_config()
        data["chat_channel_id"] = channel.id
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message(f"Brücken-Channel gesetzt auf {channel.mention}.", ephemeral=True)

    @bot.tree.command(name="set_githubupdate_channel", description="Konfiguriert Repo und Channel für GitHub-Commit-Updates")
    @app_commands.describe(repo="owner/repo", channel="Ziel-Channel", poll_interval_seconds="optional, Standard 120s")
    @app_commands.default_permissions(manage_guild=True)
    async def set_githubupdate_channel(interaction: discord.Interaction, repo: str, channel: discord.TextChannel, poll_interval_seconds: Optional[int] = None):
        repo = repo.strip()
        if "/" not in repo:
            await interaction.response.send_message("Ungültiges Repo-Format. Erwartet: owner/repo", ephemeral=True)
            return
        data = load_config()
        data["github_repo"] = repo
        data["github_updates_channel_id"] = channel.id
        if poll_interval_seconds and poll_interval_seconds > 0:
            data["github_poll_interval_seconds"] = poll_interval_seconds
        save_config(data)
        deps["apply_config"](data)
        deps["reset_last_commit"]()
        await interaction.response.send_message(f"GitHub-Updates gesetzt: {repo} → {channel.mention}.", ephemeral=True)

    @bot.tree.command(name="disable_github", description="Deaktiviert GitHub-Commit-Updates")
    @app_commands.default_permissions(manage_guild=True)
    async def disable_github(interaction: discord.Interaction):
        data = load_config()
        data.pop("github_repo", None)
        data.pop("github_updates_channel_id", None)
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message("GitHub-Updates deaktiviert.", ephemeral=True)

    @bot.tree.command(name="show_config", description="Zeigt die aktuelle Bot-Konfiguration")
    @app_commands.default_permissions(manage_guild=True)
    async def show_config(interaction: discord.Interaction):
        data = deps["collect_config_display"]()
        await interaction.response.send_message(f"```json\n{data}\n```", ephemeral=True)

    @bot.tree.command(name="change_prefix", description="Ändert das Bot-Prefix für Textcommands")
    @app_commands.describe(prefix="Neues Prefix, z. B. ! oder --")
    @app_commands.default_permissions(manage_guild=True)
    async def change_prefix(interaction: discord.Interaction, prefix: str):
        prefix = prefix.strip()
        if not prefix:
            await interaction.response.send_message("Prefix darf nicht leer sein.", ephemeral=True)
            return
        if len(prefix) > 5:
            await interaction.response.send_message("Prefix ist zu lang (max. 5 Zeichen).", ephemeral=True)
            return
        data = load_config()
        data["command_prefix"] = prefix
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message(f"Prefix geändert auf `{prefix}`.", ephemeral=True)

    @bot.tree.command(name="set_cleanup", description="Setzt Aufbewahrungsdauer und Laufintervall für Auto-Cleanup")
    @app_commands.describe(retention_hours="Stunden bis zur Löschung (z. B. 48)", interval_minutes="Intervall in Minuten (z. B. 60)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_cleanup(interaction: discord.Interaction, retention_hours: Optional[int] = None, interval_minutes: Optional[int] = None):
        changed = []
        data = load_config()
        if retention_hours is not None and retention_hours >= 0:
            data["message_cleanup_retention_hours"] = retention_hours
            changed.append(f"retention={retention_hours}h")
        if interval_minutes is not None and interval_minutes > 0:
            data["message_cleanup_interval_minutes"] = interval_minutes
            changed.append(f"interval={interval_minutes}m")
        if not changed:
            await interaction.response.send_message("Keine Änderungen übergeben.", ephemeral=True)
            return
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message("Cleanup aktualisiert: " + ", ".join(changed), ephemeral=True)

    @bot.tree.command(name="set_countdown", description="Setzt Countdown-Ziel (ISO Datum/Zeit) und Ziel-Channel")
    @app_commands.describe(target_iso="z. B. 2025-12-31T17:00", channel="Ziel-Channel", timezone_name="z. B. Europe/Berlin")
    @app_commands.default_permissions(manage_guild=True)
    async def set_countdown(interaction: discord.Interaction, target_iso: str, channel: discord.TextChannel, timezone_name: Optional[str] = None):
        tzname = timezone_name.strip() if isinstance(timezone_name, str) and timezone_name else deps["COUNTDOWN_TZ"]
        try:
            _ = deps["parse_iso_to_dt"](deps["datetime"], target_iso, tzname)
        except Exception:
            await interaction.response.send_message("Ungültiges ISO-Datum. Beispiel: 2025-12-31T17:00", ephemeral=True)
            return
        data = load_config()
        data["countdown_channel_id"] = channel.id
        data["countdown_target_iso"] = target_iso
        data["countdown_timezone"] = tzname
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message(f"Countdown gesetzt: {target_iso} ({tzname}) → {channel.mention}", ephemeral=True)

    @bot.tree.command(name="disable_countdown", description="Deaktiviert den Countdown")
    @app_commands.default_permissions(manage_guild=True)
    async def disable_countdown(interaction: discord.Interaction):
        data = load_config()
        data.pop("countdown_channel_id", None)
        data.pop("countdown_target_iso", None)
        data.pop("countdown_timezone", None)
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message("Countdown deaktiviert.", ephemeral=True)

    @bot.tree.command(name="set_countdown_role", description="Setzt die zu erwähnende Rolle für Auto-Countdowns")
    @app_commands.describe(role="Rolle, die in automatischen Countdown-Nachrichten erwähnt wird")
    @app_commands.default_permissions(administrator=True)
    async def set_countdown_role(interaction: discord.Interaction, role: discord.Role):
        data = load_config()
        data["countdown_role_id"] = role.id
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message(f"Countdown-Rolle gesetzt: {role.mention}", ephemeral=True)

    @bot.tree.command(name="disable_countdown_role", description="Entfernt die Rolle aus Auto-Countdowns")
    @app_commands.default_permissions(administrator=True)
    async def disable_countdown_role(interaction: discord.Interaction):
        data = load_config()
        data.pop("countdown_role_id", None)
        save_config(data)
        deps["apply_config"](data)
        await interaction.response.send_message("Countdown-Rolle entfernt.", ephemeral=True)

