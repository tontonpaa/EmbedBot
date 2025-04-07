import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# act.pyをインポート
import act

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.CustomActivity(name='', state="毒ユウカから毒抽出中..."))
    print(f'{bot.user.name} has connected to Discord!')

# act.pyで定義されたbotインスタンスをmain.pyのbotインスタンスと同じものにする
# act.bot = bot
async def main():
    await act.setup(bot)
    await bot.start(os.getenv('TOKEN'))

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())