import bot_vivo
bot_vivo.bot_vivo()


import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.members = True

testing = False

activity = discord.Game(name="f!help | Bot feito por onlyflushed#9949")
client = commands.Bot(command_prefix = "f!", activity=activity, case_insensitive = True, intents=intents)

client.remove_command('help')

for filename in os.listdir('./cogs'):
    if filename.endswith('.py'):
        client.load_extension(f'cogs.{filename[:-3]}')

client.run('NjczMTkxOTY1MTI2NTU3Njk2.XjWc3w.Y4yfWfp7OilhRntgzPX3INtShok')