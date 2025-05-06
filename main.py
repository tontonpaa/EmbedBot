import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from bs4 import BeautifulSoup
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()  # .envファイルを読み込む

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment variables")

# 西日本対応
from westjr import WestJR

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# JR東日本の地域別URL
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "新潟": "https://traininfo.jreast.co.jp/train_info/niigata.aspx",
    "長野": "https://traininfo.jreast.co.jp/train_info/nagano.aspx",
    "甲信越": "https://traininfo.jreast.co.jp/train_info/koshinetsu.aspx",
}

# JR東日本スクレイピング
def get_jr_east_region_info(name, url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8'
    if response.status_code != 200:
        return [{"路線名": f"[{name}]取得失敗", "運行状況": f"ステータス: {response.status_code}", "詳細": ""}]
    
    soup = BeautifulSoup(response.text, "html.parser")
    lines = soup.select(".lineDetail")
    info = []
    for line in lines:
        line_name = line.select_one(".lineName").text.strip()
        status = line.select_one(".lineStatus").text.strip()
        detail = line.select_one(".trouble span").text.strip() if line.select_one(".trouble span") else "詳細なし"
        info.append({"路線名": f"[{name}] {line_name}", "運行状況": status, "詳細": detail})
    return info

# JR西日本情報
def get_jr_west_info():
    areas = ["hokuriku", "kinki", "chugoku"]
    train_info = []
    for area in areas:
        jr = WestJR(area=area)
        statuses = jr.get_statuses()
        for s in statuses:
            name = s.get("name", "路線不明")
            status = s.get("status", "不明")
            detail = s.get("detail", "詳細なし")
            train_info.append({"路線名": f"[西日本] {name}", "運行状況": status, "詳細": detail})
    return train_info

# JR東日本 + 西日本全体取得
def get_all_train_info():
    all_info = []
    for name, url in JR_EAST_REGIONS.items():
        all_info.extend(get_jr_east_region_info(name, url))
    all_info.extend(get_jr_west_info())
    return all_info

# 埋め込み更新対象メッセージ
message_to_update = None

@tree.command(name="運行情報", description="JR全体の運行情報（関東含む）を表示します")
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    info = get_all_train_info()
    embed = discord.Embed(title="🚆 JR運行情報（東日本 + 西日本）", color=0x2e8b57)
    for line in info:
        embed.add_field(
            name=f"{line['路線名']}：{line['運行状況']}",
            value=line['詳細'],
            inline=False
        )
    embed.set_footer(text="30分ごとに自動更新されます")

    global message_to_update
    message_to_update = await interaction.followup.send(embed=embed)
    update_embed.start()

@tasks.loop(minutes=30)
async def update_embed():
    global message_to_update
    if message_to_update is None:
        return
    info = get_all_train_info()
    embed = discord.Embed(title="🚆 JR運行情報（東日本 + 西日本）", color=0x2e8b57)
    for line in info:
        embed.add_field(
            name=f"{line['路線名']}：{line['運行状況']}",
            value=line['詳細'],
            inline=False
        )
    embed.set_footer(text="30分ごとに自動更新されています")
    try:
        await message_to_update.edit(embed=embed)
    except discord.HTTPException:
        pass  # メッセージが削除されたなど

@bot.event
async def on_ready():
    print(f"Bot起動完了: {bot.user}")
    try:
        synced = await tree.sync()
        print(f"スラッシュコマンド同期済み ({len(synced)} commands)")
    except Exception as e:
        print(f"コマンド同期失敗: {e}")

    # 最後にこれを追加
if __name__ == "__main__":
    bot.run(TOKEN)