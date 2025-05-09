import os
import logging
import time
import traceback
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Environment variable DISCORD_TOKEN is not set.")

# ロギング設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot 初期化
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_EAST_AREAS = {
    "関東": 4,
    "東北": 3,
    "中部": 5,
}

# JR西日本 via Yahoo!路線情報 エリアコード
YAHOO_WEST_AREAS = {
    "近畿": 6,
    "九州": 7,
    "中国": 8,
    "四国": 9,
}

# フィルタキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# メッセージ保持用・自動投稿チャンネル
train_messages = {"east": {}, "west": {}}
REQUEST_CHANNEL = None

# ===== ヘルパー =====
def should_include(status: str, detail: str) -> bool:
    """平常運転表現を除外し、異常または詳細ありを表示対象とする"""
    normal = ["平常", "通常", "問題なく", "通常通り"]
    return not any(p in status for p in normal) or bool(detail and detail.strip())

def fetch_area_info(region: str, area_code: int) -> list[dict]:
    """
    Yahoo! 路線情報の指定エリアページから運行トラブル一覧を取得し、
    各路線ページに遷移して正確な路線名と詳細をスクレイピングする
    """
    base_url = "https://transit.yahoo.co.jp"
    url = f"{base_url}/diainfo/area/{area_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"{region} ページ取得エラー: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": str(e)}]

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    # 運行障害テーブルを持つ div.elmTblLstLine.trouble をパース
    for div in soup.select("div.elmTblLstLine.trouble"):
        tbl = div.find("table")
        if not tbl:
            continue
        for tr in tbl.select("tbody > tr")[1:]:  # ヘッダー行をスキップ
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue

            status = cols[1].get_text(strip=True)
            a_tag = cols[0].find("a", href=True)
            if not a_tag:
                continue
            link = a_tag["href"]
            line_url = base_url + link

            # 各路線ページ取得
            try:
                lr = requests.get(line_url, headers=headers, timeout=15)
                lr.raise_for_status()
            except Exception as e:
                logger.warning(f"路線ページ取得失敗 ({line_url}): {e}")
                continue

            lsoup = BeautifulSoup(lr.text, "html.parser")
            title_h1 = lsoup.select_one("div.labelLarge h1.title")
            name = title_h1.get_text(strip=True) if title_h1 else a_tag.get_text(strip=True)
            dd = lsoup.select_one("dd.trouble p")
            detail = dd.get_text(strip=True) if dd else cols[2].get_text(strip=True)

            if name and status and should_include(status, detail):
                items.append({
                    "路線名": name,
                    "運行状況": status,
                    "詳細": detail
                })

    if not items:
        return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]
    return items

# ===== 埋め込み作成 =====
def create_embed(prefix: str, region: str, data: list[dict], color: int) -> discord.Embed:
    now = (datetime.now() + timedelta(hours=9)).strftime("%Y/%m/%d %H:%M")
    title = f"🚆 {prefix}（{region}） 運行情報"
    emb = discord.Embed(title=title, description=f"最終更新: {now}", color=color)
    for x in data:
        emb.add_field(
            name=f"{x['路線名']}：{x['運行状況']}",
            value=x['詳細'] or "詳細なし",
            inline=False
        )
    return emb

# ===== エラーレポート =====
async def send_error_report(ch, message, error):
    try:
        emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
        emb.add_field(name="詳細", value=f"```\n{error}\n```", inline=False)
        await ch.send(embed=emb)
    except Exception as e:
        logger.error(f"エラーレポート送信失敗: {e}")

# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global REQUEST_CHANNEL
    try:
        if not REQUEST_CHANNEL:
            return
        ch = REQUEST_CHANNEL

        # JR東日本
        for region, code in YAHOO_EAST_AREAS.items():
            try:
                data = fetch_area_info(region, code)
                emb  = create_embed("JR東日本", region, data, 0x2E8B57)
                if region in train_messages["east"]:
                    try:
                        await train_messages["east"][region].edit(embed=emb)
                    except discord.NotFound:
                        msg = await ch.send(embed=emb)
                        train_messages["east"][region] = msg
                else:
                    msg = await ch.send(embed=emb)
                    train_messages["east"][region] = msg
            except Exception as e:
                logger.exception(f"JR東日本 {region} の更新処理でエラー")
                await send_error_report(ch, f"JR東日本 {region} 更新中にエラー", e)

        # JR西日本
        for region, code in YAHOO_WEST_AREAS.items():
            try:
                data = fetch_area_info(region, code)
                emb  = create_embed("JR西日本", region, data, 0x4682B4)
                if region in train_messages["west"]:
                    try:
                        await train_messages["west"][region].edit(embed=emb)
                    except discord.NotFound:
                        msg = await ch.send(embed=emb)
                        train_messages["west"][region] = msg
                else:
                    msg = await ch.send(embed=emb)
                    train_messages["west"][region] = msg
            except Exception as e:
                logger.exception(f"JR西日本 {region} の更新処理でエラー")
                await send_error_report(ch, f"JR西日本 {region} 更新中にエラー", e)

    except Exception as e:
        logger.error("update_train_info: 予期せぬエラーが発生しました。ループを継続します。")
        traceback.print_exc()

@update_train_info.error
async def update_train_info_error(error):
    logger.error(f"update_train_info の error ハンドラが例外をキャッチ: {error}")
    traceback.print_exc()

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel

    # JR東日本
    for region, code in YAHOO_EAST_AREAS.items():
        data = fetch_area_info(region, code)
        emb  = create_embed("JR東日本", region, data, 0x2E8B57)
        msg  = await ctx.send(embed=emb)
        train_messages["east"][region] = msg

    # JR西日本
    for region, code in YAHOO_WEST_AREAS.items():
        data = fetch_area_info(region, code)
        emb  = create_embed("JR西日本", region, data, 0x4682B4)
        msg  = await ctx.send(embed=emb)
        train_messages["west"][region] = msg

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    status = await ctx.send("🔄 手動更新中…")
    try:
        await update_train_info()
        await status.edit(content="✅ 更新完了！")
    except Exception as e:
        logger.exception("手動更新エラー")
        await status.edit(content="❌ 更新失敗")
        await send_error_report(ctx.channel, "手動更新中にエラー", e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.exception("コマンドエラー")
    await send_error_report(ctx.channel, "コマンド実行中にエラー", error)

@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    if not update_train_info.is_running():
        update_train_info.start()

if __name__ == "__main__":
    bot.run(TOKEN)
