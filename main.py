
import os
import logging
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from westjr import WestJR
from westjr.response_types import TrainInfo
from dotenv import load_dotenv

# ===== 設定 =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 環境変数から Discord トークン読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Discord トークンが.envに設定されていません。")

# Bot 初期化（message_content intent 必須）
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# HTTP ヘッダー
HEADERS = {"User-Agent": "Mozilla/5.0"}

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_AREAS = {"関東": 1, "東北": 2, "信越": 4}

# JR西日本 API エリアコード→名前
JR_WEST_AREAS = {"hokuriku": "北陸", "kinki": "近畿", "chugoku": "中国", "shikoku": "四国", "kyushu": "九州"}

# フィルタキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# ===== 補助関数 =====
def should_include(status: str, detail: str) -> bool:
    """
    運休、運転見合わせ、遅延のいずれかを含む場合にTrue
    """
    return any(kw in status for kw in DISRUPTION_KEYWORDS) or any(kw in detail for kw in DISRUPTION_KEYWORDS)

# --- JR東日本情報取得（フィルタ付き） ---
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    url = f"https://transit.yahoo.co.jp/traininfo/area/{area_code}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        info = []
        for li in soup.select("ul.linesWrap li"):
            line_name = li.select_one(".labelLine").get_text(strip=True)
            status = li.select_one(".statusTxt").get_text(strip=True)
            detail_el = li.select_one(".statusDetail")
            detail = detail_el.get_text(strip=True) if detail_el else ""
            if should_include(status, detail):
                info.append({
                    "路線名": f"[{region}] {line_name}",
                    "運行状況": status,
                    "詳細": detail or "詳細なし"
                })
        if not info:
            info = [{"路線名": f"[{region}]", "運行状況": "現在問題ありません", "詳細": ""}]
        return info
    except Exception as e:
        logger.exception(f"Yahoo経由 JR東日本 {region} 取得失敗")
        return [{"路線名": f"[{region}] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

# --- JR西日本情報取得（フィルタ付き + 明示的路線名） ---
def get_jr_west_filtered() -> list[dict]:
    info = []
    for code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            # 在来線
            for route_code, li in traffic.lines.items():
                route_name = jr.lines.get(route_code, route_code)
                if should_include(li.status, li.cause or ""):
                    info.append({
                        # 明示的に route_name フィールドを追加
                        "路線コード": route_code,
                        "路線名": f"[西日本 {area_name}] {route_name}",
                        "運行状況": li.status,
                        "詳細": li.cause or "詳細なし"
                    })
            # 特急
            for express_code, ei in traffic.express.items():
                name = ei.name
                if should_include(ei.status, ei.cause or ""):
                    info.append({
                        "路線コード": express_code,
                        "路線名": f"[西日本 {area_name} 特急] {name}",
                        "運行状況": ei.status,
                        "詳細": ei.cause or "詳細なし"
                    })
        except Exception as e:
            logger.exception(f"JR西日本 {area_name} 取得失敗")
            info.append({
                "路線名": f"[西日本 {area_name}] 取得失敗", "運行状況": "エラー", "詳細": str(e)
            })
    if not info:
        info = [{"路線名": "[西日本]", "運行状況": "現在問題ありません", "詳細": ""}]
    return info

# メッセージ保持用
global msg_east, msg_west
msg_east = {}
msg_west = None

# --- コマンド定義 ---
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global msg_east, msg_west

    # JR東日本
    for region, code in YAHOO_AREAS.items():
        data = get_jr_east_filtered(region, code)
        embed = discord.Embed(title=f"🚆 JR東日本（{region}） 運休・遅延情報", color=0x2E8B57)
        for item in data:
            embed.add_field(
                name=f"{item['路線名']}：{item['運行状況']}",
                value=item['詳細'], inline=False
            )
        msg = await ctx.send(embed=embed)
        msg_east[region] = msg

    # JR西日本
    west_data = get_jr_west_filtered()
    embed_w = discord.Embed(title="🚆 JR西日本 運休・遅延情報", color=0x4682B4)
    for item in west_data:
        embed_w.add_field(
            name=f"{item['路線名']}（コード: {item['路線コード']}）：{item['運行状況']}",
            value=item['詳細'], inline=False
        )
    msg = await ctx.send(embed=embed_w)
    msg_west = msg

# 起動時ログ
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")

bot.run(TOKEN)