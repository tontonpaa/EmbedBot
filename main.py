import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import act
import asyncio

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    activity = discord.CustomActivity(name="私は私、それだけ")
    await bot.change_presence(activity=activity)
    print(f'{bot.user.name} has connected to Discord!')

async def main():
    await act.setup(bot)
    await bot.start(os.getenv('DISCORD_TOKEN'))

if __name__ == '__main__':
    asyncio.run(main())