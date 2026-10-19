"""Plain-text Discord command tests. No Discord, Query or Minecraft connection."""
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands
from mcipc.query.proto.full_stats import FullStats

from app.commands import register_slash_commands, register_text_commands
from app.ui import StatusReader, address_text, countdown_text
from app.tasks import _countdown_tick


def config(**values):
    settings = dict(countdown_target_iso=None, countdown_timezone="Europe/Vienna",
                    server_address="mc.example.test", command_prefix="mc!",
                    chat_channel_id=123, has_query=True, server_ip="10.0.0.77", query_port=25565)
    settings.update(values)
    return SimpleNamespace(**settings)


def interaction(channel=123):
    return SimpleNamespace(channel_id=channel,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class MenuFormatting(unittest.TestCase):
    def test_address_is_hidden_until_real_countdown_then_public_address_only(self):
        target = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
        cfg = config(countdown_target_iso=target.isoformat())
        self.assertEqual(address_text(cfg, target - timedelta(seconds=1)),
                         "Dieser Command ist erst ab dem 24.10.2026 um 18:00 Uhr (CEST) verfügbar.")
        self.assertIn(cfg.server_address, address_text(cfg, target))
        self.assertNotIn(cfg.server_ip, address_text(cfg, target))
        self.assertIn("Verbleibende Zeit", countdown_text(cfg, target - timedelta(hours=1)))

    def test_invalid_countdown_fails_closed_and_disabled_countdown_matches_ip_command(self):
        cfg = config(countdown_target_iso="2026-10-24T18:00", countdown_timezone="Not/AZone")
        self.assertNotIn(cfg.server_address, address_text(cfg))
        self.assertIn("ungültig", countdown_text(cfg))
        cfg.countdown_target_iso = None
        self.assertIn(cfg.server_address, address_text(cfg))


class MenuInteractions(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_blocked_before_start_even_with_cached_status(self):
        reader = StatusReader()
        reader._query = Mock(return_value=dict(players=["Alex"], num_players=1, max_players=20))
        cfg = config()
        self.assertIn("Alex", await reader.read(cfg, 123))
        cfg.countdown_target_iso = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        blocked = await reader.read(cfg, 123)
        self.assertIn("Dieser Command ist erst ab dem", blocked)
        for private in ("Alex", "Server online", cfg.server_ip, cfg.server_address):
            self.assertNotIn(private, blocked)
        self.assertEqual(reader._query.call_count, 1)

    async def test_status_unlocks_exactly_at_start_and_invalid_countdown_blocks_query(self):
        reader = StatusReader()
        reader._query = Mock(return_value=dict(players=[], num_players=0, max_players=20))
        target = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
        cfg = config(countdown_target_iso="2026-10-24T18:00")
        with patch("app.ui.datetime", wraps=datetime) as clock:
            clock.now.return_value = target - timedelta(seconds=1)
            self.assertEqual(await reader.read(cfg, 123),
                             "Dieser Command ist erst ab dem 24.10.2026 um 18:00 Uhr (CEST) verfügbar.")
            reader._query.assert_not_called()
            clock.now.return_value = target
            self.assertIn("0/20", await reader.read(cfg, 123))
        cfg.countdown_timezone = "Not/AZone"
        self.assertIn("gültigen Countdown", await reader.read(cfg, 123))
        self.assertEqual(reader._query.call_count, 1)

    async def test_ping_and_address_commands_follow_changed_start_date(self):
        bot = commands.Bot(command_prefix="mc!", intents=discord.Intents.none())
        try:
            cfg = config(countdown_target_iso=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
            register_text_commands(bot, cfg)
            reader = StatusReader()
            reader._query = Mock(return_value=dict(players=[], num_players=0, max_players=20))
            with patch("app.commands.STATUS", reader):
                for name in ("ping", "ip", "serverip"):
                    ctx = SimpleNamespace(send=AsyncMock(), channel=SimpleNamespace(id=123))
                    await bot.get_command(name).callback(ctx)
                    self.assertIn("Dieser Command ist erst ab dem", ctx.send.await_args.args[0])
                    self.assertNotIn(cfg.server_address, ctx.send.await_args.args[0])
                reader._query.assert_not_called()
                cfg.countdown_target_iso = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                for name, expected in (("ping", "0/20"), ("ip", cfg.server_address), ("serverip", cfg.server_address)):
                    ctx = SimpleNamespace(send=AsyncMock(), channel=SimpleNamespace(id=123))
                    await bot.get_command(name).callback(ctx)
                    self.assertIn(expected, ctx.send.await_args.args[0])
        finally:
            await bot.close()

    async def test_status_uses_real_fullstats_type_on_worker_and_caches(self):
        reader = StatusReader()
        fixture = FullStats(None, 1, "Server", "SMP", "MINECRAFT", "1.18.2", {},
                            "private-world", 2, 20, 25565, "10.0.0.77", ["Alex", "Sam"])
        threads = []
        def query(*_):
            threads.append(threading.current_thread().name)
            return fixture
        reader._query = Mock(side_effect=query)
        cfg = config()
        first = await reader.read(cfg, 123)
        second = await reader.read(cfg, 123)
        self.assertIn("2/20", first)
        self.assertIn("Alex", first)
        self.assertNotIn(cfg.server_ip, first)
        self.assertNotIn("private-world", first)
        self.assertEqual(first, second)
        self.assertEqual(reader._query.call_count, 1)
        self.assertNotEqual(threads[0], threading.current_thread().name)

    async def test_status_rechecks_channel_after_configuration_changes_even_if_cached(self):
        reader = StatusReader()
        reader._query = Mock(return_value=dict(players=[], num_players=0, max_players=20))
        cfg = config()
        await reader.read(cfg, 123)
        cfg.chat_channel_id = 999
        rejected = await reader.read(cfg, 123)
        self.assertIn("Mirror", rejected)
        self.assertEqual(reader._query.call_count, 1)
        self.assertIn("0/20", await reader.read(cfg, 999))

    async def test_query_failure_does_not_expose_internal_exception_or_host(self):
        reader = StatusReader()
        reader._query = Mock(side_effect=RuntimeError("private password and 10.0.0.77"))
        result = await reader.read(config(), 123)
        self.assertNotIn("password", result)
        self.assertNotIn("10.0.0.77", result)
        self.assertIn("verfügbar", result)

    async def test_server_commands_are_plain_text_without_button_views(self):
        bot = commands.Bot(command_prefix="mc!", intents=discord.Intents.none())
        try:
            cfg = config()
            cfg.set = Mock(side_effect=AssertionError("read-only commands must not mutate config"))
            register_text_commands(bot, cfg)
            with patch("app.commands.STATUS", SimpleNamespace(read=AsyncMock(return_value="Server online"))):
                for name in ("menu", "ping", "ip", "wielange"):
                    ctx = SimpleNamespace(send=AsyncMock(), channel=SimpleNamespace(id=123))
                    await bot.get_command(name).callback(ctx)
                    self.assertNotIn("view", ctx.send.await_args.kwargs)
                    self.assertIsInstance(ctx.send.await_args.args[0], str)
            cfg.set.assert_not_called()
        finally:
            await bot.close()

    async def test_plain_address_command_reads_current_countdown_gate(self):
        bot = commands.Bot(command_prefix="mc!", intents=discord.Intents.none())
        try:
            cfg = config()
            register_text_commands(bot, cfg)
            cfg.countdown_target_iso = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
            ctx = SimpleNamespace(send=AsyncMock())
            await bot.get_command("ip").callback(ctx)
            self.assertNotIn(cfg.server_address, ctx.send.await_args.args[0])
            self.assertNotIn("view", ctx.send.await_args.kwargs)
        finally:
            await bot.close()

    async def test_automatic_countdown_and_final_timer_messages_have_no_views(self):
        cfg = config(countdown_channel_id=123, countdown_last_auto_message_id=None,
                     countdown_role_id=None, countdown_timer_message="Ready {ip}",
                     countdown_timer_message_sent=False,
                     countdown_target_iso=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
        cfg.set = Mock()
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=42)))
        bot = SimpleNamespace(get_channel=Mock(return_value=channel))
        with patch("app.tasks.countdown_message", return_value="Countdown"):
            await _countdown_tick(bot, cfg)
        self.assertNotIn("view", channel.send.await_args.kwargs)
        cfg.countdown_target_iso = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        await _countdown_tick(bot, cfg)
        self.assertIn(cfg.server_address, channel.send.await_args.args[0])
        self.assertNotIn("view", channel.send.await_args.kwargs)

    async def test_menu_text_and_slash_commands_register_without_admin_mutations(self):
        bot = commands.Bot(command_prefix="mc!", intents=discord.Intents.none())
        try:
            cfg = config()
            register_text_commands(bot, cfg)
            register_slash_commands(bot, cfg)
            self.assertIs(bot.get_command("menu"), bot.get_command("hilfe"))
            ctx = SimpleNamespace(send=AsyncMock())
            await bot.get_command("menu").callback(ctx)
            self.assertNotIn("view", ctx.send.await_args.kwargs)
            click = interaction()
            await bot.tree.get_command("server_menu").callback(click)
            self.assertTrue(click.response.send_message.await_args.kwargs["ephemeral"])
            self.assertNotIn("view", click.response.send_message.await_args.kwargs)
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()
