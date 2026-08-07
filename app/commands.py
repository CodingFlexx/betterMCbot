import json
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from mcipc.query import Client as QueryClient
from mcipc.rcon.je import Client as RconClient

from app.tasks import (
    countdown_reached,
    delete_message,
    format_time_delta,
    parse_iso_to_aware_dt,
    render_timer_message,
)


def register_text_commands(bot: commands.Bot, cfg):
    @bot.command(name="whitelistadd")
    async def whitelistadd(ctx, *, arg):
        if cfg.chat_channel_id and ctx.channel.id != cfg.chat_channel_id:
            return
        if not cfg.has_rcon:
            await ctx.send("Minecraft-RCON ist nicht konfiguriert.")
            return
        try:
            with RconClient(cfg.server_ip, cfg.rcon_port, passwd=cfg.rcon_password) as client:
                client.whitelist.add(arg)
            await ctx.send(f"Spieler {arg} wurde zur Whitelist hinzugefügt")
        except Exception:
            await ctx.send("Server nicht erreichbar")

    @bot.command(name="ping")
    async def ping(ctx):
        if cfg.chat_channel_id and ctx.channel.id != cfg.chat_channel_id:
            return
        if not cfg.has_query:
            await ctx.send("Minecraft-Query ist nicht konfiguriert.")
            return
        try:
            with QueryClient(cfg.server_ip, cfg.query_port) as client:
                status = client.stats(full=True)
            lines = [f"Server ist online mit {status['num_players']}/{status['max_players']} Spielern:"]
            lines += [f"\t{player}" for player in status["players"]]
            await ctx.send("\n".join(lines))
        except Exception:
            await ctx.send("Server ist offline")

    @bot.command(name="ip", aliases=["serverip"])
    async def ip(ctx):
        if not cfg.server_address:
            await ctx.send("Keine Server-Adresse hinterlegt.")
            return
        # Vor dem Start bleibt die Adresse unter Verschluss
        if not countdown_reached(cfg):
            tz_name = cfg.countdown_timezone
            remaining = parse_iso_to_aware_dt(cfg.countdown_target_iso, tz_name) - datetime.now(ZoneInfo(tz_name))
            await ctx.send(f"Noch nicht — der Server startet in {format_time_delta(remaining)}.")
            return
        await ctx.send(f"Server-IP: `{cfg.server_address}`")

    @bot.command(name="wielange", aliases=["countdown"])
    async def wielange(ctx):
        if not cfg.countdown_target_iso:
            await ctx.send("Kein Countdown-Ziel gesetzt.")
            return
        tz_name = cfg.countdown_timezone
        remaining = parse_iso_to_aware_dt(cfg.countdown_target_iso, tz_name) - datetime.now(ZoneInfo(tz_name))
        if remaining.total_seconds() <= 0:
            await ctx.send("Der Zeitpunkt ist bereits erreicht.")
            return

        # Alte Countdown-Nachrichten und den vorherigen Aufruf aufräumen
        for message_id in {cfg.countdown_last_message_id, cfg.countdown_last_auto_message_id}:
            await delete_message(ctx.channel, message_id, bot=bot)
        if cfg.countdown_last_trigger_id != ctx.message.id:
            await delete_message(ctx.channel, cfg.countdown_last_trigger_id, bot_only=False)

        sent = await ctx.send(f"Verbleibende Zeit: {format_time_delta(remaining)}")
        cfg.set(countdown_last_message_id=sent.id, countdown_last_trigger_id=ctx.message.id)


def register_slash_commands(bot: commands.Bot, cfg):
    @bot.tree.command(name="set_server_channel", description="Setzt den Discord-Channel für die Minecraft-Brücke")
    @app_commands.describe(channel="Ziel-Channel für Brücke")
    @app_commands.default_permissions(manage_guild=True)
    async def set_server_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        cfg.set(chat_channel_id=channel.id)
        await interaction.response.send_message(f"Brücken-Channel gesetzt auf {channel.mention}.", ephemeral=True)

    @bot.tree.command(name="set_githubupdate_channel", description="Konfiguriert Repo und Channel für GitHub-Commit-Updates")
    @app_commands.describe(repo="owner/repo", channel="Ziel-Channel", poll_interval_seconds="optional, Standard 120s")
    @app_commands.default_permissions(manage_guild=True)
    async def set_githubupdate_channel(
        interaction: discord.Interaction,
        repo: str,
        channel: discord.TextChannel,
        poll_interval_seconds: Optional[int] = None,
    ):
        repo = repo.strip()
        if "/" not in repo:
            await interaction.response.send_message("Ungültiges Repo-Format. Erwartet: owner/repo", ephemeral=True)
            return
        cfg.set(github_repo=repo, github_updates_channel_id=channel.id)
        if poll_interval_seconds and poll_interval_seconds > 0:
            cfg.set(github_poll_interval_seconds=poll_interval_seconds)
        await interaction.response.send_message(f"GitHub-Updates gesetzt: {repo} → {channel.mention}.", ephemeral=True)

    @bot.tree.command(name="disable_github", description="Deaktiviert GitHub-Commit-Updates")
    @app_commands.default_permissions(manage_guild=True)
    async def disable_github(interaction: discord.Interaction):
        cfg.set(github_repo=None, github_updates_channel_id=None)
        await interaction.response.send_message("GitHub-Updates deaktiviert.", ephemeral=True)

    @bot.tree.command(name="show_config", description="Zeigt die aktuelle Bot-Konfiguration")
    @app_commands.default_permissions(manage_guild=True)
    async def show_config(interaction: discord.Interaction):
        data = json.dumps(cfg.as_dict(), ensure_ascii=False, indent=2)
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
        cfg.set(command_prefix=prefix)
        await interaction.response.send_message(f"Prefix geändert auf `{prefix}`.", ephemeral=True)

    @bot.tree.command(name="set_cleanup", description="Setzt Aufbewahrungsdauer und Laufintervall für Auto-Cleanup")
    @app_commands.describe(retention_hours="Stunden bis zur Löschung (z. B. 48)", interval_minutes="Intervall in Minuten (z. B. 60)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_cleanup(
        interaction: discord.Interaction,
        retention_hours: Optional[int] = None,
        interval_minutes: Optional[int] = None,
    ):
        changed = []
        if retention_hours is not None and retention_hours >= 0:
            cfg.set(message_cleanup_retention_hours=retention_hours)
            changed.append(f"retention={retention_hours}h")
        if interval_minutes is not None and interval_minutes > 0:
            cfg.set(message_cleanup_interval_minutes=interval_minutes)
            changed.append(f"interval={interval_minutes}m")
        if not changed:
            await interaction.response.send_message("Keine Änderungen übergeben.", ephemeral=True)
            return
        await interaction.response.send_message("Cleanup aktualisiert: " + ", ".join(changed), ephemeral=True)

    @bot.tree.command(name="set_countdown", description="Setzt Countdown-Ziel (ISO Datum/Zeit) und Ziel-Channel")
    @app_commands.describe(target_iso="z. B. 2025-12-31T17:00", channel="Ziel-Channel", timezone_name="z. B. Europe/Berlin")
    @app_commands.default_permissions(manage_guild=True)
    async def set_countdown(
        interaction: discord.Interaction,
        target_iso: str,
        channel: discord.TextChannel,
        timezone_name: Optional[str] = None,
    ):
        tz_name = (timezone_name or cfg.countdown_timezone).strip()
        try:
            ZoneInfo(tz_name)
            datetime.fromisoformat(target_iso)
        except Exception:
            await interaction.response.send_message(
                "Ungültiges ISO-Datum oder Zeitzone. Beispiel: 2025-12-31T17:00 / Europe/Berlin", ephemeral=True
            )
            return
        cfg.set(
            countdown_channel_id=channel.id,
            countdown_target_iso=target_iso,
            countdown_timezone=tz_name,
            countdown_timer_message_sent=False,
        )
        await interaction.response.send_message(
            f"Countdown gesetzt: {target_iso} ({tz_name}) → {channel.mention}", ephemeral=True
        )

    @bot.tree.command(name="disable_countdown", description="Deaktiviert den Countdown")
    @app_commands.default_permissions(manage_guild=True)
    async def disable_countdown(interaction: discord.Interaction):
        cfg.set(countdown_channel_id=None, countdown_target_iso=None)
        await interaction.response.send_message("Countdown deaktiviert.", ephemeral=True)

    @bot.tree.command(name="set_countdown_role", description="Setzt die zu erwähnende Rolle für Auto-Countdowns")
    @app_commands.describe(role="Rolle, die in automatischen Countdown-Nachrichten erwähnt wird")
    @app_commands.default_permissions(administrator=True)
    async def set_countdown_role(interaction: discord.Interaction, role: discord.Role):
        cfg.set(countdown_role_id=role.id)
        await interaction.response.send_message(f"Countdown-Rolle gesetzt: {role.mention}", ephemeral=True)

    @bot.tree.command(name="disable_countdown_role", description="Entfernt die Rolle aus Auto-Countdowns")
    @app_commands.default_permissions(administrator=True)
    async def disable_countdown_role(interaction: discord.Interaction):
        cfg.set(countdown_role_id=None)
        await interaction.response.send_message("Countdown-Rolle entfernt.", ephemeral=True)

    @bot.tree.command(name="set_server_address", description="Setzt die öffentliche Join-Adresse des Minecraft-Servers")
    @app_commands.describe(address="z. B. mc.example.com oder 1.2.3.4:25565")
    @app_commands.default_permissions(manage_guild=True)
    async def set_server_address(interaction: discord.Interaction, address: str):
        address = address.strip()
        if not address:
            await interaction.response.send_message("Adresse darf nicht leer sein.", ephemeral=True)
            return
        cfg.set(server_address=address)
        hint = "" if countdown_reached(cfg) else " Bis zum Countdown-Ende bleibt sie unter Verschluss."
        await interaction.response.send_message(f"Server-Adresse gesetzt: `{address}`.{hint}", ephemeral=True)

    @bot.tree.command(name="set_timer_message", description="Speichert eine Nachricht, die beim Ablauf des Timers gesendet wird")
    @app_commands.describe(message="Nachricht beim Timer-Ablauf. {ip} wird durch die Server-Adresse ersetzt")
    @app_commands.default_permissions(manage_guild=True)
    async def set_timer_message(interaction: discord.Interaction, message: str):
        message = message.strip()
        if not message:
            await interaction.response.send_message("Die Nachricht darf nicht leer sein.", ephemeral=True)
            return
        cfg.set(countdown_timer_message=message, countdown_timer_message_sent=False)
        preview = render_timer_message(message, cfg.server_address or "<noch nicht gesetzt>")
        await interaction.response.send_message(
            f"Timer-Nachricht gespeichert. Vorschau:\n```\n{preview}\n```", ephemeral=True
        )

    @bot.tree.command(name="clear_timer_message", description="Entfernt die gespeicherte Timer-Nachricht")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_timer_message(interaction: discord.Interaction):
        cfg.set(countdown_timer_message=None, countdown_timer_message_sent=None)
        await interaction.response.send_message("Timer-Nachricht entfernt.", ephemeral=True)
