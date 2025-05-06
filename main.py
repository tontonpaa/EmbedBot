import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from bs4 import BeautifulSoup
import asyncio
from dotenv import load_dotenv
import os
from datetime import datetime

load_dotenv()  # .envファイルを読み込む

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment variables")

# 西日本対応
from westjr import WestJR

intents = discord.Intents.all()
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

# --- 更新対象をまとめるための変数 --- 
message_to_update_east = {}  # 東日本各地域の更新対象
message_to_update_west = None  # 西日本全体の更新対象

# --- JR東日本スクレイピング --- 
def get_jr_east_region_info(name, url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
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
    except Exception as e:
        return [{"路線名": f"[{name}]取得失敗", "運行状況": "タイムアウトまたはエラー", "詳細": str(e)}]

# --- JR西日本スクレイピング ---
def get_jr_west_info():
    areas = ["hokuriku", "kinki", "chugoku"]
    train_info = []
    for area in areas:
        try:
            jr = WestJR(area=area)
            statuses = jr.get_statuses()
            for s in statuses:
                name = s.get("name", "路線不明")
                status = s.get("status", "不明")
                detail = s.get("detail", "詳細なし")
                train_info.append({"路線名": f"[西日本] {name}", "運行状況": status, "詳細": detail})
        except Exception as e:
            train_info.append({"路線名": f"[西日本] {area}", "運行状況": "取得失敗", "詳細": str(e)})
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
        message_to_update_east[name] = await interaction.followup.send(embed=embed)  # 各地域ごとの更新対象を格納
    
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
    message_to_update_west = await interaction.followup.send(embed=embed)  # 西日本の更新対象を格納

    update_embed.start()  # 自動更新タスクを開始


# --- 自動更新タスク --- 
@tasks.loop(minutes=30)
async def update_embed():
    # 東日本の更新
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
        # メッセージを更新
        if name in message_to_update_east:
            await message_to_update_east[name].edit(embed=embed)

    # 西日本の更新
    west_info = get_jr_west_info()
    embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
    for line in west_info:
        embed.add_field(
            name=f"{line['路線名']}：{line['運行状況']}",
            value=line['詳細'],
            inline=False
        )
    embed.set_footer(text="30分ごとに自動更新されます")
    # 西日本のメッセージを更新
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

    # 最後にこれを追加
if __name__ == "__main__":
    bot.run(TOKEN)