import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import logging
from westjr import WestJR
from westjr.response_types import TrainInfo

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# .envから Discord トークンを読み込む
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment variables")

# Discord Bot 設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# JR東日本 各地域のURL
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "新潟": "https://traininfo.jreast.co.jp/train_info/niigata.aspx",
    "長野": "https://traininfo.jreast.co.jp/train_info/nagano.aspx",
    "甲信越": "https://traininfo.jreast.co.jp/train_info/koshinetsu.aspx",
}

# 自動更新用メッセージ保持
message_to_update_east = {}
message_to_update_west = None

# HTTP ヘッダー (ボットとしてブロック回避)
HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- JR東日本: requests + BeautifulSoup でスクレイピング ---
def get_jr_east_region_info(name: str, url: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        lines = soup.select(".lineDetail")
        info = []
        for line in lines:
            title = line.select_one(".lineName").get_text(strip=True)
            status = line.select_one(".lineStatus").get_text(strip=True)
            detail_tag = line.select_one(".trouble span")
            detail = detail_tag.get_text(strip=True) if detail_tag else "詳細なし"
            info.append({
                "路線名": f"[{name}] {title}",
                "運行状況": status,
                "詳細": detail
            })
        if not info:
            info.append({"路線名": f"[{name}]","運行状況": "なし","詳細": "情報が見つかりませんでした。"})
        return info
    except Exception as e:
        logger.exception(f"JR東日本 {name} 取得エラー")
        return [{"路線名": f"[{name}] 取得失敗","運行状況": "エラー","詳細": str(e)}]

# --- JR西日本: WestJR API から取得 ---
def get_jr_west_info() -> list[dict]:
    info = []
    try:
        jr = WestJR(area="kinki")
        traffic: TrainInfo = jr.get_traffic_info()
        # 在来線情報
        for key, li in traffic.lines.items():
            section = f"{li.section.from_ or ''}→{li.section.to or ''}"
            info.append({
                "路線名": f"[西日本] {section}",
                "運行状況": li.status,
                "詳細": li.cause
            })
        # 特急情報
        for key, ei in traffic.express.items():
            info.append({
                "路線名": f"[西日本 特急] {ei.name}",
                "運行状況": ei.status,
                "詳細": ei.cause
            })
        if not info:
            info.append({"路線名": "[西日本]","運行状況": "なし","詳細": "問題ありません。"})
        return info
    except Exception as e:
        logger.exception("JR西日本 取得エラー")
        return [{"路線名": "[西日本] 取得失敗","運行状況": "エラー","詳細": str(e)}]

# --- スラッシュコマンド ---
@tree.command(name="運行情報", description="JR東日本・西日本の運行情報を表示します")
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    global message_to_update_east, message_to_update_west
    try:
        # 東日本 各地域
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2e8b57)
            for item in info:
                embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed.set_footer(text="30分ごとに自動更新されます")
            msg = await interaction.followup.send(embed=embed)
            message_to_update_east[region] = msg
        # 西日本
        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
        for item in west_info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text="30分ごとに自動更新されます")
        message_to_update_west = await interaction.followup.send(embed=embed)
        if not update_embed.is_running():
            update_embed.start()
    except Exception as e:
        logger.exception("コマンド実行中にエラーが発生しました。")
        await interaction.followup.send("運行情報の取得中にエラーが発生しました。")

# --- 定期更新タスク ---
@tasks.loop(minutes=30)
async def update_embed():
    global message_to_update_east, message_to_update_west
    try:
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2e8b57)
            for item in info:
                embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed.set_footer(text="30分ごとに自動更新されます")
            if region in message_to_update_east:
                await message_to_update_east[region].edit(embed=embed)
        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
        for item in west_info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text="30分ごとに自動更新されます")
        if message_to_update_west:
            await message_to_update_west.edit(embed=embed)
    except Exception as e:
        logging.exception("定期更新中にエラーが発生しました。")

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logging.info(f"Bot 起動成功: {bot.user}")
    except Exception as e:
        logging.error(f"スラッシュコマンド同期失敗: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)
