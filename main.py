import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
import logging
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from westjr import WestJR
from westjr.response_types import TrainInfo

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 環境変数からトークン読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment variables")

# Discord Bot 設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# JR東日本 各地域のURL (新潟・長野・甲信越を除き、信越を追加)
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "信越": "https://traininfo.jreast.co.jp/train_info/shinetsu.aspx",
}

# 自動更新用メッセージ保持
message_to_update_east = {}
message_to_update_west = None

# Selenium WebDriver を1回だけ初期化
chrome_options = Options()
chrome_options.binary_location = "/usr/bin/chromium"
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
service = Service("/usr/bin/chromedriver")
driver = webdriver.Chrome(service=service, options=chrome_options)

# --- JR東日本: Selenium + BeautifulSoup ---
def get_jr_east_region_info(name: str, url: str) -> list[dict]:
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "lineDetail"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")
        lines = soup.select(".lineDetail")
        info = []
        for line in lines:
            title = line.select_one(".lineName").get_text(strip=True)
            status = line.select_one(".lineStatus").get_text(strip=True)
            detail_tag = line.select_one(".trouble span")
            detail = detail_tag.get_text(strip=True) if detail_tag else "詳細なし"
            info.append({"路線名": f"[{name}] {title}", "運行状況": status, "詳細": detail})
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
        for code, li in traffic.lines.items():
            route_name = jr.lines.get(code, code)
            info.append({
                "路線名": f"[西日本] {route_name}",
                "運行状況": li.status,
                "詳細": li.cause or "詳細なし"
            })
        # 特急情報
        for key, ei in traffic.express.items():
            info.append({
                "路線名": f"[西日本 特急] {ei.name}",
                "運行状況": ei.status,
                "詳細": ei.cause or "詳細なし"
            })
        if not info:
            info.append({"路線名": "[西日本]","運行状況": "なし","詳細": "問題ありません。"})
        return info
    except Exception as e:
        logger.exception("JR西日本 取得エラー")
        return [{"路線名": "[西日本] 取得失敗","運行状況": "エラー","詳細": str(e)}]

# --- /運行情報 コマンド ---
@tree.command(name="運行情報", description="JR東日本・西日本の運行情報を表示します")
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    global message_to_update_east, message_to_update_west
    try:
        # 東日本
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2e8b57)
            for item in info:
                embed.add_field(
                    name=f"{item['路線名']}：{item['運行状況']}",
                    value=item['詳細'],
                    inline=False
                )
            embed.set_footer(text="30分ごとに自動更新されます")
            msg = await interaction.followup.send(embed=embed)
            message_to_update_east[region] = msg
        # 西日本
        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
        for item in west_info:
            embed.add_field(
                name=f"{item['路線名']}：{item['運行状況']}",
                value=item['詳細'],
                inline=False
            )
        embed.set_footer(text="30分ごとに自動更新されます")
        message_to_update_west = await interaction.followup.send(embed=embed)
        # タスク起動
        if not update_embed.is_running(): update_embed.start()
    except Exception as e:
        logger.exception("コマンド実行中にエラーが発生しました。")
        await interaction.followup.send("運行情報の取得中にエラーが発生しました。")

# --- 自動更新タスク ---
@tasks.loop(minutes=30)
async def update_embed():
    global message_to_update_east, message_to_update_west
    try:
        # 東日本 更新
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2e8b57)
            for item in info:
                embed.add_field(
                    name=f"{item['路線名']}：{item['運行状況']}",
                    value=item['詳細'],
                    inline=False
                )
            embed.set_footer(text="30分ごとに自動更新されます")
            if region in message_to_update_east:
                await message_to_update_east[region].edit(embed=embed)
        # 西日本 更新
        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本運行情報", color=0x4682b4)
        for item in west_info:
            embed.add_field(
                name=f"{item['路線名']}：{item['運行状況']}",
                value=item['詳細'],
                inline=False
            )
        embed.set_footer(text="30分ごとに自動更新されます")
        if message_to_update_west:
            await message_to_update_west.edit(embed=embed)
    except Exception as e:
        logger.exception("定期更新中にエラーが発生しました。")

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logger.info(f"Bot 起動成功: {bot.user}")
    except Exception as e:
        logger.error(f"スラッシュコマンド同期失敗: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)