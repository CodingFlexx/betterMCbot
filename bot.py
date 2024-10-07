import discord
from discord.ext import commands
from dotenv import load_dotenv
from mcipc.rcon.je import Client
from mcipc.query import Client as QueryClient
import os

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_IP = os.getenv("SERVER_IP")
SERVER_PORT = os.getenv("SERVER_PORT")
RCON_PORT = os.getenv("RCON_PORT")
RCON_PASSWORD = os.getenv("RCON_PASSWORD")
QUERY_PORT = os.getenv("QUERY_PORT")
CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID")

discord_command_prefix = "-"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(description="Discord Chatbot", command_prefix=discord_command_prefix, intents=intents)


@bot.event
async def on_ready():
    print('Bot Ready!')


@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if (await bot.get_context(message)).command is not None:
        return
    if message.author == bot.user or not message.channel.id == int(CHAT_CHANNEL_ID):
        return
    with Client(SERVER_IP, int(RCON_PORT), passwd=RCON_PASSWORD) as client:
        client.say("[Discord] " + message.author.name + ": " + message.content)


@bot.command(name='whitelist')
async def whitelist(ctx, *, arg):
    name = arg
    try:
        with Client(SERVER_IP, int(RCON_PORT), passwd=RCON_PASSWORD) as client:
            whitelist = client.whitelist
            whitelist.add(name)
            await ctx.send("Spieler " + name + " wurde zur Whitelist hinzugefügt")
    except Exception as e:
        await ctx.send("Server nicht erreichbar")


@bot.command(name='ping')
async def ping(ctx):
    print("HI")
    try:
        with QueryClient(SERVER_IP, int(QUERY_PORT)) as client:
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
