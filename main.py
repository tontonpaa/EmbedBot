import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import act
import EmbedCommands
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
    await bot.tree.sync() # グローバルコマンドを同期 (推奨)

async def main():
    await act.setup(bot)
    await EmbedCommands.setup(bot)  # EmbedCommands の setup 関数を呼び出して Cog を追加
    await bot.start(os.getenv('DISCORD_TOKEN'))

if __name__ == '__main__':
    asyncio.run(main())