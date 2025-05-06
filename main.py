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

# Bot 初期化（intents 必須）
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 全路線ページ
JR_EAST_ALL_URL = "https://traininfo.jreast.co.jp/train_info/everywhere.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# JR西日本 対応エリア
JR_WEST_AREAS = {
    "hokuriku": "北陸",
    "kinki": "近畿",
    "chugoku": "中国",
    "shikoku": "四国",
    "kyushu": "九州",
}

# 定期更新用メッセージ保持
global msg_east, msg_west
msg_east = None
msg_west = None

# JR東日本 情報取得
def get_jr_east_all_info() -> list[dict]:
    try:
        resp = requests.get(JR_EAST_ALL_URL, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        info = []
        for div in soup.select(".lineDetail"):
            line_name = div.select_one(".lineName").get_text(strip=True)
            status = div.select_one(".lineStatus").get_text(strip=True)
            detail_span = div.select_one(".trouble span")
            detail = detail_span.get_text(strip=True) if detail_span else "詳細なし"
            info.append({"路線名": f"[東日本] {line_name}", "運行状況": status, "詳細": detail})
        if not info:
            info = [{"路線名": "[東日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
        return info
    except Exception as e:
        logger.exception("JR東日本 取得エラー")
        return [{"路線名": "[東日本] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

# JR西日本 情報取得
def get_jr_west_info() -> list[dict]:
    info = []
    for code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            for route_code, li in traffic.lines.items():
                route_name = jr.lines.get(route_code, route_code)
                info.append({"路線名": f"[西日本 {area_name}] {route_name}", "運行状況": li.status, "詳細": li.cause or "詳細なし"})
            for _, ei in traffic.express.items():
                info.append({"路線名": f"[西日本 {area_name} 特急] {ei.name}", "運行状況": ei.status, "詳細": ei.cause or "詳細なし"})
        except requests.exceptions.HTTPError as he:
            logger.warning(f"西日本 {area_name} スキップ: {he}")
        except Exception as e:
            logger.exception(f"JR西日本 {area_name} 取得エラー")
            info.append({"路線名": f"[西日本 {area_name}] 取得失敗", "運行状況": "エラー", "詳細": str(e)})
    if not info:
        info = [{"路線名": "[西日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
    return info

# コマンド
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global msg_east, msg_west
    east = get_jr_east_all_info()
    embed_e = discord.Embed(title="🚆 JR東日本 全路線運行情報", color=0x2E8B57)
    for item in east:
        embed_e.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
    embed_e.set_footer(text="30分ごとに自動更新されます")
    msg_east = await ctx.send(embed=embed_e)

    west = get_jr_west_info()
    embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
    for item in west:
        embed_w.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
    embed_w.set_footer(text="30分ごとに自動更新されます")
    msg_west = await ctx.send(embed=embed_w)

    if not periodic_update.is_running():
        periodic_update.start()

# 定期更新タスク
@tasks.loop(minutes=30)
async def periodic_update():
    global msg_east, msg_west
    try:
        if msg_east:
            east = get_jr_east_all_info()
            embed_e = discord.Embed(title="🚆 JR東日本 全路線運行情報", color=0x2E8B57)
            for item in east:
                embed_e.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed_e.set_footer(text="30分ごとに自動更新されます")
            await msg_east.edit(embed=embed_e)
        if msg_west:
            west = get_jr_west_info()
            embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
            for item in west:
                embed_w.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed_w.set_footer(text="30分ごとに自動更新されます")
            await msg_west.edit(embed=embed_w)
    except Exception:
        logger.exception("定期更新中にエラー発生")

# 起動時ログ
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")

bot.run(TOKEN)