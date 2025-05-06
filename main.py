import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from bs4 import BeautifulSoup
import asyncio
from dotenv import load_dotenv
import os
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

options = Options()
options.binary_location = "/usr/bin/chromium"
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

# ChromeDriverを直接指定
service = Service("/usr/bin/chromedriver")
driver = webdriver.Chrome(service=service, options=options)


# 西日本対応
from westjr import WestJR

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment variables")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "新潟": "https://traininfo.jreast.co.jp/train_info/niigata.aspx",
    "長野": "https://traininfo.jreast.co.jp/train_info/nagano.aspx",
    "甲信越": "https://traininfo.jreast.co.jp/train_info/koshinetsu.aspx",
}

message_to_update_east = {}
message_to_update_west = None

# --- JR東日本スクレイピング (Selenium使用) ---
def get_jr_east_region_info(name, url):
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    try:
        driver.get(url)
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        lines = soup.select(".lineDetail")
        info = []
        for line in lines:
            line_name = line.select_one(".lineName").text.strip()
            status = line.select_one(".lineStatus").text.strip()
            detail = line.select_one(".trouble span").text.strip() if line.select_one(".trouble span") else "詳細なし"
            info.append({"路線名": f"[{name}] {line_name}", "運行状況": status, "詳細": detail})
        return info
    except Exception as e:
        return [{"路線名": f"[{name}]取得失敗", "運行状況": "タイムアウトまたはエラー", "詳細": str(e)}]
    finally:
        driver.quit()

# --- JR西日本 ---
def get_jr_west_info():
    train_info = []
    try:
        jr = WestJR()
        statuses = jr.fetch()
        for s in statuses:
            name = s.get("name", "路線不明")
            status = s.get("status", "不明")
            detail = s.get("text", "詳細なし")
            train_info.append({"路線名": f"[西日本] {name}", "運行状況": status, "詳細": detail})
    except Exception as e:
        train_info.append({"路線名": "[西日本] 全体", "運行状況": "取得失敗", "詳細": str(e)})
    return train_info

# --- コマンド ---
@tree.command(name="運行情報", description="JR全体の運行情報（関東含む）を表示します")
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()

    # 東日本：地域ごとにembed分割
    for name, url in JR_EAST_REGIONS.items():
        info = get_jr_east_region_info(name, url)
        embed = discord.Embed(title=f"🚆 JR東日本（{name}）運行情報", color=0x2e8b57)
        for line in info:
            embed.add_field(
                name=f"{line['路線名']}：{line['運行状況']}",
                value=line['詳細'],
                inline=False
            )
        embed.set_footer(text="30分ごとに自動更新されます")
        message_to_update_east[name] = await interaction.followup.send(embed=embed)
    
    # 西日本：1つのembedにまとめて送信
    west_info = get_jr_west_info()
    embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
    for line in west_info:
        embed.add_field(
            name=f"{line['路線名']}：{line['運行状況']}",
            value=line['詳細'],
            inline=False
        )
    embed.set_footer(text="30分ごとに自動更新されます")
    global message_to_update_west
    message_to_update_west = await interaction.followup.send(embed=embed)

    update_embed.start()

# --- 自動更新タスク ---
@tasks.loop(minutes=30)
async def update_embed():
    for name, url in JR_EAST_REGIONS.items():
        info = get_jr_east_region_info(name, url)
        embed = discord.Embed(title=f"🚆 JR東日本（{name}）運行情報", color=0x2e8b57)
        for line in info:
            embed.add_field(
                name=f"{line['路線名']}：{line['運行状況']}",
                value=line['詳細'],
                inline=False
            )
        embed.set_footer(text="30分ごとに自動更新されます")
        if name in message_to_update_east:
            await message_to_update_east[name].edit(embed=embed)

    west_info = get_jr_west_info()
    embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
    for line in west_info:
        embed.add_field(
            name=f"{line['路線名']}：{line['運行状況']}",
            value=line['詳細'],
            inline=False
        )
    embed.set_footer(text="30分ごとに自動更新されます")
    if message_to_update_west:
        await message_to_update_west.edit(embed=embed)

@bot.event
async def on_ready():
    print(f"Bot起動完了: {bot.user}")
    try:
        synced = await tree.sync()
        print(f"スラッシュコマンド同期済み ({len(synced)} commands)")
    except Exception as e:
        print(f"コマンド同期失敗: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)
