import os
import json
import logging
import traceback
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STATE_FILE = "state.json"

# ロギング設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot 初期化
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本エリアコード（Yahoo!路線情報）
YAHOO_EAST_AREAS = {"関東": 4, "東北": 3, "中部": 5}
# JR西日本エリアコード
YAHOO_WEST_AREAS = {"近畿": 6, "九州": 7, "中国": 8, "四国": 9}

# 検知するキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延", "その他", "運転計画", "運行情報"]

# メッセージID保持
train_messages = {"east": {}, "west": {}}
REQUEST_CHANNEL = None
update_counter = 0

# ===== 状態保存/復元 =====
def load_state():
    global REQUEST_CHANNEL, train_messages
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            REQUEST_CHANNEL = state.get("channel_id")
            train_messages = state.get("messages", {"east": {}, "west": {}})
            logger.info("状態を復元しました")
    except Exception as e:
        logger.warning(f"状態復元に失敗: {e}")

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "channel_id": REQUEST_CHANNEL,
                "messages": train_messages
            }, f, ensure_ascii=False, indent=2)
            logger.info("状態を保存しました")
    except Exception as e:
        logger.error(f"状態保存に失敗: {e}")

# ===== ヘルパー =====
def should_include(status: str, detail: str) -> bool:
    """DISRUPTION_KEYWORDS を含むものだけを返す"""
    return any(kw in status for kw in DISRUPTION_KEYWORDS) or any(kw in detail for kw in DISRUPTION_KEYWORDS)

def fetch_area_info(region: str, area_code: int) -> list[dict]:
    """
    指定エリアページの <div class="elmTblLstLine"> を全てチェックし、
    テーブルの各行について DISRUPTION_KEYWORDS を検知した路線をリンク先まで辿って取得。
    """
    base_url = "https://transit.yahoo.co.jp"
    url = f"{base_url}/diainfo/area/{area_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"{region} ページ取得エラー: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": str(e)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # 全ての elmTblLstLine div を走査
    for div in soup.select("div.elmTblLstLine"):
        tbl = div.find("table")
        if not tbl:
            continue

        # ヘッダー行スキップ
        for tr in tbl.select("tbody > tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue

            status = cols[1].get_text(strip=True)
            detail_preview = cols[2].get_text(strip=True)

            # キーワードがなければスキップ
            if not should_include(status, detail_preview):
                continue

            # リンクをたどる
            a_tag = cols[0].find("a", href=True)
            if not a_tag:
                continue
            link = base_url + a_tag["href"]

            # 詳細ページ取得
            try:
                lr = requests.get(link, headers=headers, timeout=60)
                lr.raise_for_status()
            except Exception as e:
                logger.warning(f"路線ページ取得失敗 ({link}): {e}")
                continue

            lsoup = BeautifulSoup(lr.text, "html.parser")

            # 正式な路線名
            title_h1 = lsoup.select_one("div.labelLarge h1.title")
            name = title_h1.get_text(strip=True) if title_h1 else a_tag.get_text(strip=True)

            # 詳細説明
            dd = lsoup.select_one("dd.trouble p")
            detail = dd.get_text(strip=True) if dd else detail_preview

            items.append({
                "路線名": name,
                "運行状況": status,
                "詳細": detail
            })

    return items if items else [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]

# ===== 埋め込み作成 =====
def create_embed(prefix: str, region: str, data: list[dict], color: int) -> discord.Embed:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M")
    title = f"🚆 {prefix}（{region}） 運行情報"
    emb = discord.Embed(title=title, description=f"最終更新: {now}", color=color)
    for x in data:
        emb.add_field(
            name=f"{x['路線名']}：{x['運行状況']}",
            value=x['詳細'] or "詳細なし",
            inline=False
        )
    return emb

async def send_error_report(ch, message, error):
    try:
        emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
        emb.add_field(name="詳細", value=f"```\n{error}\n```", inline=False)
        await ch.send(embed=emb)
    except Exception as e:
        logger.error(f"エラーレポート送信失敗: {e}")

# ===== 自動更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global update_counter
    update_counter += 1
    logger.info(f"[#{update_counter}] auto-update start")

    if not REQUEST_CHANNEL:
        logger.info("REQUEST_CHANNEL not set, skipping")
        return

    ch = bot.get_channel(REQUEST_CHANNEL)
    if not ch:
        logger.warning("保存されたチャンネルが見つかりません")
        return

    # 東日本
    for region, code in YAHOO_EAST_AREAS.items():
        try:
            data = fetch_area_info(region, code)
            emb = create_embed("JR東日本", region, data, 0x2E8B57)
            msg_id = train_messages["east"].get(region)
            if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    await msg.edit(embed=emb)
                except discord.NotFound:
                    msg = await ch.send(embed=emb)
            else:
                msg = await ch.send(embed=emb)
            train_messages["east"][region] = msg.id
        except Exception as e:
            await send_error_report(ch, f"JR東日本 {region} 更新エラー", e)

    # 西日本
    for region, code in YAHOO_WEST_AREAS.items():
        try:
            data = fetch_area_info(region, code)
            emb = create_embed("JR西日本", region, data, 0x4682B4)
            msg_id = train_messages["west"].get(region)
            if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    await msg.edit(embed=emb)
                except discord.NotFound:
                    msg = await ch.send(embed=emb)
            else:
                msg = await ch.send(embed=emb)
            train_messages["west"][region] = msg.id
        except Exception as e:
            await send_error_report(ch, f"JR西日本 {region} 更新エラー", e)

    save_state()

@update_train_info.error
async def update_train_info_error(err):
    logger.error(f"update_train_info error handler caught: {err}")
    traceback.print_exc()

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel.id
    save_state()

    ch = ctx.channel

    for region, code in YAHOO_EAST_AREAS.items():
        data = fetch_area_info(region, code)
        emb = create_embed("JR東日本", region, data, 0x2E8B57)
        msg = await ch.send(embed=emb)
        train_messages["east"][region] = msg.id

    for region, code in YAHOO_WEST_AREAS.items():
        data = fetch_area_info(region, code)
        emb = create_embed("JR西日本", region, data, 0x4682B4)
        msg = await ch.send(embed=emb)
        train_messages["west"][region] = msg.id

    save_state()

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
    logger.exception("コマンド実行エラー")
    await send_error_report(ctx.channel, "コマンド実行中にエラー", error)

@bot.event
async def on_ready():
    logger.info(f"Bot ready: {bot.user}")
    load_state()
    if not update_train_info.is_running():
        update_train_info.start()

if __name__ == "__main__":
    bot.run(TOKEN)
