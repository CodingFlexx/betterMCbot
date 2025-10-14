import discord
from discord.ext import commands
from dotenv import load_dotenv
from mcipc.rcon.je import Client
from mcipc.query import Client as QueryClient
import logging
import os

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_IP = os.getenv("SERVER_IP")
RCON_PORT = os.getenv("RCON_PORT")
RCON_PASSWORD = os.getenv("RCON_PASSWORD")
QUERY_PORT = os.getenv("QUERY_PORT")
CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("betterMCbot")

if not TOKEN:
    raise SystemExit("Fehlende Environment Variable: DISCORD_TOKEN")

def _parse_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None

RCON_PORT_INT = _parse_int(RCON_PORT)
QUERY_PORT_INT = _parse_int(QUERY_PORT)
CHAT_CHANNEL_ID_INT = _parse_int(CHAT_CHANNEL_ID)

HAS_RCON = bool(SERVER_IP and RCON_PASSWORD and RCON_PORT_INT)
HAS_QUERY = bool(SERVER_IP and QUERY_PORT_INT)
HAS_BRIDGE = bool(HAS_RCON and CHAT_CHANNEL_ID_INT)

discord_command_prefix = "-"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(description="Discord Chatbot", command_prefix=discord_command_prefix, intents=intents)


@bot.event
async def on_ready():
    logger.info("Bot Ready als %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    logger.info("Verbunden mit %d Guild(s)", len(bot.guilds))
    logger.info(
        "Features: bridge=%s, rcon=%s, query=%s",
        "on" if HAS_BRIDGE else "off",
        "on" if HAS_RCON else "off",
        "on" if HAS_QUERY else "off",
    )


@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if (await bot.get_context(message)).command is not None:
        return
    if message.author == bot.user or message.author.bot:
        return
    if not HAS_BRIDGE:
        return
    if message.channel.id != CHAT_CHANNEL_ID_INT:
        return
    try:
        with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
            client.say("[Discord] " + message.author.name + ": " + message.content)
    except Exception as exc:
        logger.warning("RCON Send fehlgeschlagen: %s", exc)


@bot.command(name='whitelist')
async def whitelist(ctx, *, arg):
    name = arg
    try:
        if not HAS_RCON:
            await ctx.send("Minecraft-RCON ist nicht konfiguriert.")
            return
        with Client(SERVER_IP, RCON_PORT_INT, passwd=RCON_PASSWORD) as client:
            whitelist = client.whitelist
            whitelist.add(name)
            await ctx.send("Spieler " + name + " wurde zur Whitelist hinzugefügt")
    except Exception as e:
        await ctx.send("Server nicht erreichbar")


@bot.command(name='ping')
async def ping(ctx):
    try:
        if not HAS_QUERY:
            await ctx.send("Minecraft-Query ist nicht konfiguriert.")
            return
        with QueryClient(SERVER_IP, QUERY_PORT_INT) as client:
            status = client.stats(full=True)
            ans = "Server ist online mit " + str(status['num_players']) + "/" + str(
                status['max_players']) + " Spielern:"
            for player in status['players']:
                ans += "\n\t" + player
            await ctx.send(ans)
    except Exception as e:
        await ctx.send("Server ist offline")


#        if status['online'] == 0:
#            await ctx.send("Server is offline")
#        else:
#            res =


bot.run(TOKEN)
