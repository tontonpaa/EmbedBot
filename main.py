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

# --- JR東日本 via Yahoo!路線情報 ---
# Yahoo!エリアコード: 1=関東, 2=東北, 4=信越・北陸
YAHOO_AREAS = {"関東": 1, "東北": 2, "信越": 4}

def get_jr_east_via_yahoo(region: str, area_code: int) -> list[dict]:
    url = f"https://transit.yahoo.co.jp/traininfo/area/{area_code}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        info = []
        # 各路線情報は ul.linesWrap > li
        for li in soup.select("ul.linesWrap li"):
            line_name = li.select_one(".labelLine").get_text(strip=True)
            status = li.select_one(".statusTxt").get_text(strip=True)
            detail_el = li.select_one(".statusDetail")
            detail = detail_el.get_text(strip=True) if detail_el else ""
            info.append({
                "路線名": f"[{region}] {line_name}",
                "運行状況": status,
                "詳細": detail or "詳細なし"
            })
        if not info:
            info = [{"路線名": f"[{region}]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
        return info
    except Exception as e:
        logger.exception(f"Yahoo経由 JR東日本 {region} 取得失敗")
        return [{"路線名": f"[{region}] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]

# --- JR西日本 via WestJR API ---
JR_WEST_AREAS = {"hokuriku": "北陸", "kinki": "近畿", "chugoku": "中国", "shikoku": "四国", "kyushu": "九州"}

def get_jr_west_info() -> list[dict]:
    info = []
    for code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            # 在来線
            for route_code, li in traffic.lines.items():
                name = jr.lines.get(route_code, route_code)
                info.append({
                    "路線名": f"[西日本 {area_name}] {name}",
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
            logger.warning(f"西日本 {area_name} API未対応: {he}")
        except Exception as e:
            logger.exception(f"JR西日本 {area_name} 取得失敗")
            info.append({
                "路線名": f"[西日本 {area_name}] 取得失敗",
                "運行状況": "エラー",
                "詳細": str(e)
            })
    if not info:
        info = [{"路線名": "[西日本]", "運行状況": "なし", "詳細": "情報が見つかりませんでした。"}]
    return info

# メッセージ保持用
msg_east: dict[str, discord.Message] = {}
msg_west: discord.Message | None = None

# --- コマンド定義 --- @bot.commandでプレフィックス対応
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global msg_east, msg_west

    # 東日本: 地域ごとにEmbed
    for region, code in YAHOO_AREAS.items():
        data = get_jr_east_via_yahoo(region, code)
        embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2E8B57)
        for item in data:
            embed.add_field(
                name=f"{item['路線名']}：{item['運行状況']}",
                value=item['詳細'],
                inline=False
            )
        embed.set_footer(text="30分ごとに自動更新されます")
        msg = await ctx.send(embed=embed)
        msg_east[region] = msg

    # 西日本: 一つのEmbed
    west_data = get_jr_west_info()
    embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
    for item in west_data:
        embed_w.add_field(
            name=f"{item['路線名']}：{item['運行状況']}",
            value=item['詳細'],
            inline=False
        )
    embed_w.set_footer(text="30分ごとに自動更新されます")
    msg = await ctx.send(embed=embed_w)
    msg_west = msg

    # 定期更新開始
    if not periodic_update.is_running():
        periodic_update.start()

# --- 定期更新 (30分) ---
@tasks.loop(minutes=30)
async def periodic_update():
    global msg_east, msg_west
    try:
        # 東日本更新
        for region, code in YAHOO_AREAS.items():
            data = get_jr_east_via_yahoo(region, code)
            embed = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", color=0x2E8B57)
            for item in data:
                embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed.set_footer(text="30分ごとに自動更新されます")
            await msg_east[region].edit(embed=embed)
        # 西日本更新
        data = get_jr_west_info()
        embed_w = discord.Embed(title="🚆 JR西日本 運行情報", color=0x4682B4)
        for item in data:
            embed_w.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed_w.set_footer(text="30分ごとに自動更新されます")
        if msg_west:
            await msg_west.edit(embed=embed_w)
    except Exception:
        logger.exception("定期更新中にエラー発生")

# 起動時ログ
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")

# 実行
bot.run(TOKEN)
