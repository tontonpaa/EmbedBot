import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging
from westjr import WestJR

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# JR東日本地域URL
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "新潟": "https://traininfo.jreast.co.jp/train_info/niigata.aspx",
    "長野": "https://traininfo.jreast.co.jp/train_info/nagano.aspx",
    "甲信越": "https://traininfo.jreast.co.jp/train_info/koshinetsu.aspx",
}

# Discord設定
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

message_to_update_east = {}
message_to_update_west = None

# ChromeDriver 再利用のための設定
chrome_options = Options()
chrome_options.binary_location = "/usr/bin/chromium"
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--remote-debugging-port=9222")
service = Service("/usr/bin/chromedriver")
driver = webdriver.Chrome(service=service, options=chrome_options)

def get_jr_east_region_info(name, url):
    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "lineDetail")))
        soup = BeautifulSoup(driver.page_source, "html.parser")
        lines = soup.select(".lineDetail")
        info = []
        for line in lines:
            line_name = line.select_one(".lineName").text.strip()
            status = line.select_one(".lineStatus").text.strip()
            detail = line.select_one(".trouble span").text.strip() if line.select_one(".trouble span") else "詳細なし"
            info.append({"路線名": f"[{name}] {line_name}", "運行状況": status, "詳細": detail})
        return info
    except Exception as e:
        logger.exception(f"JR東日本 - {name} 情報取得失敗")
        return [{"路線名": f"[{name}] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

def get_jr_west_info():
    info = []
    try:
        jr = WestJR(area="kinki")
        traffic_info = jr.get_traffic_info()
        if isinstance(traffic_info, list):
            for status in traffic_info:
                section = status.section.from_ + " - " + status.section.to
                info.append({"路線名": f"[西日本] {section}", "運行状況": status.status, "詳細": status.cause})
        else:
            info.append({"路線名": "[西日本] 不明", "運行状況": "取得形式不明", "詳細": str(traffic_info)})
    except Exception as e:
        logger.exception("JR西日本 情報取得エラー")
        info.append({"路線名": "[西日本] 全体", "運行状況": "取得失敗", "詳細": str(e)})
    return info

@tree.command(name="運行情報", description="JR東日本/西日本の運行情報を表示")
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    global message_to_update_east, message_to_update_west

    try:
        # 東日本
        for name, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(name, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{name}）", color=0x2e8b57)
            for line in info:
                embed.add_field(name=f"{line['路線名']}：{line['運行状況']}", value=line['詳細'], inline=False)
            embed.set_footer(text="30分ごとに自動更新されます")
            message = await interaction.followup.send(embed=embed)
            message_to_update_east[name] = message

        # 西日本
        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本", color=0x4682b4)
        for line in west_info:
            embed.add_field(name=f"{line['路線名']}：{line['運行状況']}", value=line['詳細'], inline=False)
        embed.set_footer(text="30分ごとに自動更新されます")
        message_to_update_west = await interaction.followup.send(embed=embed)

        if not update_embed.is_running():
            update_embed.start()

    except Exception as e:
        logger.exception("運行情報コマンドでエラー")
        await interaction.followup.send("運行情報の取得中にエラーが発生しました。")

@tasks.loop(minutes=30)
async def update_embed():
    global message_to_update_east, message_to_update_west
    try:
        for name, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(name, url)
            embed = discord.Embed(title=f"🚆 JR東日本（{name}）", color=0x2e8b57)
            for line in info:
                embed.add_field(name=f"{line['路線名']}：{line['運行状況']}", value=line['詳細'], inline=False)
            embed.set_footer(text="30分ごとに自動更新されます")
            if name in message_to_update_east:
                await message_to_update_east[name].edit(embed=embed)

        west_info = get_jr_west_info()
        embed = discord.Embed(title="🚆 JR西日本", color=0x4682b4)
        for line in west_info:
            embed.add_field(name=f"{line['路線名']}：{line['運行状況']}", value=line['詳細'], inline=False)
        embed.set_footer(text="30分ごとに自動更新されます")
        if message_to_update_west:
            await message_to_update_west.edit(embed=embed)

    except Exception as e:
        logger.exception("自動更新中のエラー")

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logger.info(f"Bot 起動成功: {bot.user}")
    except Exception as e:
        logger.error(f"スラッシュコマンド同期失敗: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)
