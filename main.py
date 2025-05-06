import os
import logging
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from westjr import WestJR
from westjr.response_types import TrainInfo

# ========= 設定 =========
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Discord トークンが.envに設定されていません。")

bot = commands.Bot(command_prefix="!")

# JR東日本 全路線ページ
JR_EAST_ALL_URL = "https://traininfo.jreast.co.jp/train_info/everywhere.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# JR西日本対応エリア
JR_WEST_AREAS = {
    "hokuriku": "北陸",
    "kinki": "近畿",
    "chugoku": "中国",
    "shikoku": "四国",
    "kyushu": "九州",
}

# メッセージ編集用保持
msg_east = None
msg_west = None

# ========= JR東日本 の情報取得 =========
def get_jr_east_all_info() -> list[dict]:
    """
    JR東日本 全路線ページから .lineDetail をスクレイピングして
    {'路線名': '○○線', '運行状況': '運転見合わせ', '詳細': '原因…'} のリストを返す
    """
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
            detail = detail_span.get_text(strip=True) if detail_span else ""
            info.append({
                "路線名": f"[東日本] {line_name}",
                "運行状況": status,
                "詳細": detail or "詳細なし"
            })
        if not info:
            info = [{"路線名": "[東日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
        return info
    except Exception as e:
        logger.exception("JR東日本 取得エラー")
        return [{"路線名": "[東日本] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

# ========= JR西日本 の情報取得 =========
def get_jr_west_info() -> list[dict]:
    """
    WestJR API で在来線・特急をすべて取得し、
    {'路線名': '[西日本 近畿] ○○線', '運行状況': '遅延', '詳細':'…'} のリストを返す
    """
    info = []
    for code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            # 在来線
            for route_code, li in traffic.lines.items():
                route_name = jr.lines.get(route_code, route_code)
                info.append({
                    "路線名": f"[西日本 {area_name}] {route_name}",
                    "運行状況": li.status,
                    "詳細": li.cause or "詳細なし"
                })
            # 特急
            for _, ei in traffic.express.items():
                info.append({
                    "路線名": f"[西日本 {area_name} 特急] {ei.name}",
                    "運行状況": ei.status,
                    "詳細": ei.cause or "詳細なし"
                })
        except requests.exceptions.HTTPError as he:
            # API未対応エリアはスキップ
            logger.warning(f"西日本 {area_name} APIスキップ: {he}")
        except Exception as e:
            logger.exception(f"JR西日本 {area_name} 取得エラー")
            info.append({
                "路線名": f"[西日本 {area_name}] 取得失敗",
                "運行状況": "エラー",
                "詳細": str(e)
            })
    if not info:
        info = [{"路線名": "[西日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
    return info

# ========= コマンド & タスク =========
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    """手動で現在の運行情報を送信"""
    global msg_east, msg_west

    # 東日本
    east = get_jr_east_all_info()
    embed_e = discord.Embed(title="🚆 JR東日本 全路線運行情報", color=0x2E8B57)
    for item in east:
        embed_e.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item["詳細"], inline=False)
    embed_e.set_footer(text="30分ごとに自動更新されます")
    msg = await ctx.send(embed=embed_e)
    msg_east = msg

    # 西日本
    west = get_jr_west_info()
    embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
    for item in west:
        embed_w.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item["詳細"], inline=False)
    embed_w.set_footer(text="30分ごとに自動更新されます")
    msg = await ctx.send(embed=embed_w)
    msg_west = msg

    # 定期更新開始
    if not periodic_update.is_running():
        periodic_update.start()

@tasks.loop(minutes=30)
async def periodic_update():
    """30分ごとに既存メッセージを更新"""
    global msg_east, msg_west
    try:
        if msg_east:
            east = get_jr_east_all_info()
            embed_e = discord.Embed(title="🚆 JR東日本 全路線運行情報", color=0x2E8B57)
            for item in east:
                embed_e.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item["詳細"], inline=False)
            embed_e.set_footer(text="30分ごとに自動更新されます")
            await msg_east.edit(embed=embed_e)

        if msg_west:
            west = get_jr_west_info()
            embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
            for item in west:
                embed_w.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item["詳細"], inline=False)
            embed_w.set_footer(text="30分ごとに自動更新されます")
            await msg_west.edit(embed=embed_w)
    except Exception:
        logger.exception("定期更新中にエラー発生")

# Bot 起動
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")

bot.run(TOKEN)
