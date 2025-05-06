import os
import logging
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from westjr import WestJR
from westjr.response_types import TrainInfo
from dotenv import load_dotenv

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 環境変数から Discord トークン読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Discord トークンが.envに設定されていません。")

# Bot 初期化（MESSAGE_CONTENT intent を有効に）
intents = discord.Intents.all()
intents.message_content = True  # プレフィックスコマンドを使うには必須
bot = commands.Bot(command_prefix="!", intents=intents)

# HTTPヘッダー
HEADERS = {"User-Agent": "Mozilla/5.0"}

# JR東日本 各地域URL
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "信越": "https://traininfo.jreast.co.jp/train_info/shinetsu.aspx",
}

# JR西日本 エリアコード→名前
JR_WEST_AREAS = {
    "hokuriku": "北陸",
    "kinki": "近畿",
    "chugoku": "中国",
    "shikoku": "四国",
    "kyushu": "九州",
}

# --- JR東日本情報取得 ---
def get_jr_east_region_info(region: str, url: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 403:
            return [{"路線名": f"[{region}]", "運行状況": "メンテナンス中", "詳細": ""}]
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        info = []
        for div in soup.select('.lineDetail'):
            name = div.select_one('.lineName').get_text(strip=True)
            status = div.select_one('.lineStatus').get_text(strip=True)
            detail_tag = div.select_one('.trouble span')
            detail = detail_tag.get_text(strip=True) if detail_tag else '詳細なし'
            info.append({"路線名": f"[{region}] {name}", "運行状況": status, "詳細": detail})
        if not info:
            info = [{"路線名": f"[{region}]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
        return info
    except Exception as e:
        logger.exception(f"JR東日本 {region} 取得失敗")
        return [{"路線名": f"[{region}] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

# --- JR西日本情報取得 ---
def get_jr_west_info() -> list[dict]:
    info = []
    for code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            for route_code, li in traffic.lines.items():
                route_name = jr.lines.get(route_code, route_code)
                info.append({"路線名": f"[西日本 {area_name}] {route_name}", "運行状況": li.status, "詳細": li.cause or '詳細なし'})
            for _, ei in traffic.express.items():
                info.append({"路線名": f"[西日本 {area_name} 特急] {ei.name}", "運行状況": ei.status, "詳細": ei.cause or '詳細なし'})
        except requests.exceptions.HTTPError as he:
            logger.warning(f"西日本 {area_name} API未対応: {he}")
        except Exception as e:
            logger.exception(f"JR西日本 {area_name} 取得失敗")
            info.append({"路線名": f"[西日本 {area_name}] 取得失敗", "運行状況": "エラー", "詳細": str(e)})
    if not info:
        info = [{"路線名": "[西日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
    return info

# --- コマンド定義 ---
@bot.command(name='運行情報')
async def train_info(ctx: commands.Context):
    # JR東日本
    for region, url in JR_EAST_REGIONS.items():
        info = get_jr_east_region_info(region, url)
        embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2e8b57)
        for item in info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text="30分ごとに自動更新されます")
        await ctx.send(embed=embed)

    # JR西日本
    west_info = get_jr_west_info()
    embed = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682b4)
    for item in west_info:
        embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
    embed.set_footer(text="30分ごとに自動更新されます")
    await ctx.send(embed=embed)

# 起動時ログ
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")

bot.run(TOKEN)