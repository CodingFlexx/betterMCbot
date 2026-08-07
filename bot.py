import logging

import discord
from discord.ext import commands
from mcipc.rcon.je import Client as RconClient

from app import tasks
from app.commands import register_slash_commands, register_text_commands
from app.config import Config
from app.webhooks import start_web_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("betterMCbot")

cfg = Config()
if not cfg.discord_token:
    raise SystemExit("Fehlende Environment Variable: DISCORD_TOKEN")


def get_command_prefix(_bot, message):
    prefixes = [cfg.command_prefix]
    # Im Mirror-Channel zusätzlich das klassische "-" erlauben
    if cfg.chat_channel_id and message.channel.id == cfg.chat_channel_id:
        prefixes.append("-")
    return prefixes


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(description="Discord Chatbot", command_prefix=get_command_prefix, intents=intents)

register_text_commands(bot, cfg)
register_slash_commands(bot, cfg)


@bot.event
async def setup_hook():
    bot.loop.create_task(tasks.github_updates_task(bot, cfg))
    bot.loop.create_task(tasks.message_cleanup_task(bot, cfg))
    bot.loop.create_task(tasks.countdown_task(bot, cfg))
    if cfg.webhooks_enabled:
        bot.loop.create_task(start_web_server(bot, cfg))
    try:
        await bot.tree.sync()
        logger.info("Slash-Commands synchronisiert")
    except Exception as exc:
        logger.warning("Slash-Commands Sync fehlgeschlagen: %s", exc)


@bot.event
async def on_ready():
    logger.info("Bot bereit als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    logger.info("Verbunden mit %d Guild(s), Config: %s", len(bot.guilds), cfg.path)
    logger.info(
        "Features: bridge=%s rcon=%s query=%s github=%s webhooks=%s",
        cfg.has_bridge, cfg.has_rcon, cfg.has_query, cfg.has_github, cfg.webhooks_enabled,
    )


@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if message.author.bot or not cfg.has_bridge:
        return
    if message.channel.id != cfg.chat_channel_id:
        return
    if (await bot.get_context(message)).command is not None:
        return
    try:
        with RconClient(cfg.server_ip, cfg.rcon_port, passwd=cfg.rcon_password) as client:
            client.say(f"[Discord] {message.author.name}: {message.content}")
    except Exception as exc:
        logger.warning("RCON Send fehlgeschlagen: %s", exc)


bot.run(cfg.discord_token)
