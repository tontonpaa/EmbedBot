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

# グローバル変数で Cog がロード済みかどうかを管理する (簡易的な対策)
EMBED_COMMANDS_LOADED = False

async def main():
    global EMBED_COMMANDS_LOADED
    await act.setup(bot)
    if not EMBED_COMMANDS_LOADED:
        try:
            await EmbedCommands.setup(bot)
            EMBED_COMMANDS_LOADED = True
            print("EmbedCommands loaded successfully.")
        except discord.ClientException as e:
            print(f"Error loading EmbedCommands: {e}")
    else:
        print("EmbedCommands already loaded.")
    await bot.start(os.getenv('DISCORD_TOKEN'))

if __name__ == '__main__':
    asyncio.run(main())