import os
import json
import logging
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

MESSAGE_FILE = "message_ids.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot 初期化
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_EAST_AREAS = {"関東": 4, "東北": 3, "中部": 5}
# JR西日本 via Yahoo!路線情報 エリアコード
YAHOO_WEST_AREAS = {"近畿": 6, "九州": 7, "中国": 8, "四国": 9}

DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

train_messages = {"east": {}, "west": {}}
REQUEST_CHANNEL = None
update_counter = 0

# ===== 永続化 =====
def load_message_ids():
    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"east": {}, "west": {}}
    except Exception as e:
        logger.error(f"message_ids.json 読み込みエラー: {e}")
        return {"east": {}, "west": {}}

def save_message_ids(data):
    try:
        with open(MESSAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"message_ids.json 書き込みエラー: {e}")

# ===== ヘルパー =====
def should_include(status: str, detail: str) -> bool:
    normal = ["平常", "通常", "問題なく", "通常通り"]
    return not any(p in status for p in normal) or bool(detail and detail.strip())

def fetch_area_info(region: str, area_code: int) -> list[dict]:
    base = "https://transit.yahoo.co.jp"
    url = f"{base}/diainfo/area/{area_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"{region} ページ取得エラー: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": str(e)}]

    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for div in soup.select("div.elmTblLstLine.trouble"):
        tbl = div.find("table")
        if not tbl:
            continue
        for tr in tbl.select("tbody > tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            status = cols[1].get_text(strip=True)
            a = cols[0].find("a", href=True)
            if not a:
                continue
            link = base + a["href"]
            try:
                lr = requests.get(link, headers=headers, timeout=15)
                lr.raise_for_status()
            except Exception as e:
                logger.warning(f"路線ページ取得失敗 ({link}): {e}")
                continue
            lsoup = BeautifulSoup(lr.text, "html.parser")
            title = lsoup.select_one("div.labelLarge h1.title")
            name = title.get_text(strip=True) if title else a.get_text(strip=True)
            dd = lsoup.select_one("dd.trouble p")
            detail = dd.get_text(strip=True) if dd else cols[2].get_text(strip=True)
            if name and status and should_include(status, detail):
                items.append({"路線名": name, "運行状況": status, "詳細": detail})

    if not items:
        return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]
    return items

def create_embed(prefix: str, region: str, data: list[dict], color: int) -> discord.Embed:
    now = (datetime.now() + timedelta(hours=9)).strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(title=f"🚆 {prefix}（{region}） 運行情報",
                        description=f"最終更新: {now}", color=color)
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}",
                      value=x['詳細'] or "詳細なし", inline=False)
    return emb

async def send_error_report(ch, msg, err):
    try:
        emb = discord.Embed(title="🔴 エラー発生", description=msg, color=0xFF0000)
        emb.add_field(name="詳細", value=f"```\n{err}\n```", inline=False)
        await ch.send(embed=emb)
    except Exception as e:
        logger.error(f"エラーレポート送信失敗: {e}")

# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global REQUEST_CHANNEL, update_counter
    update_counter += 1
    logger.info(f"自動更新開始 #{update_counter}")

    try:
        if REQUEST_CHANNEL is None:
            logger.info("REQUEST_CHANNEL 未設定、スキップ")
            return
        ch = REQUEST_CHANNEL

        # 東日本
        for region, code in YAHOO_EAST_AREAS.items():
            try:
                logger.info(f"[#{update_counter}] 東日本 {region} 更新")
                data = fetch_area_info(region, code)
                emb = create_embed("JR東日本", region, data, 0x2E8B57)
                msg_obj = train_messages["east"].get(region)
                if msg_obj:
                    try:
                        await msg_obj.edit(embed=emb)
                    except discord.NotFound:
                        msg_obj = await ch.send(embed=emb)
                        train_messages["east"][region] = msg_obj
                else:
                    msg_obj = await ch.send(embed=emb)
                    train_messages["east"][region] = msg_obj
                logger.info(f"[#{update_counter}] 東日本 {region} 更新完了")
            except Exception as e:
                logger.exception(f"東日本 {region} 更新エラー")
                await send_error_report(ch, f"東日本 {region} 更新中にエラー", e)

        # 西日本
        for region, code in YAHOO_WEST_AREAS.items():
            try:
                logger.info(f"[#{update_counter}] 西日本 {region} 更新")
                data = fetch_area_info(region, code)
                emb = create_embed("JR西日本", region, data, 0x4682B4)
                msg_obj = train_messages["west"].get(region)
                if msg_obj:
                    try:
                        await msg_obj.edit(embed=emb)
                    except discord.NotFound:
                        msg_obj = await ch.send(embed=emb)
                        train_messages["west"][region] = msg_obj
                else:
                    msg_obj = await ch.send(embed=emb)
                    train_messages["west"][region] = msg_obj
                logger.info(f"[#{update_counter}] 西日本 {region} 更新完了")
            except Exception as e:
                logger.exception(f"西日本 {region} 更新エラー")
                await send_error_report(ch, f"西日本 {region} 更新中にエラー", e)

        logger.info(f"自動更新完了 #{update_counter}")

    except Exception as e:
        logger.error(f"予期せぬエラーでループ継続（#{update_counter}）: {e}")
        traceback.print_exc()

@update_train_info.error
async def update_train_info_error(err):
    logger.error(f"update_train_info の error ハンドラで例外: {err}")
    traceback.print_exc()

# ===== !運行情報 コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL

    REQUEST_CHANNEL = ctx.channel
    saved = load_message_ids()

    # 東日本
    for region, code in YAHOO_EAST_AREAS.items():
        data = fetch_area_info(region, code)
        emb = create_embed("JR東日本", region, data, 0x2E8B57)

        # 前回メッセージIDがあれば編集、それ以外は新規送信
        entry = saved["east"].get(region)
        if entry:
            ch = bot.get_channel(entry["channel_id"])
            try:
                msg_obj = await ch.fetch_message(entry["message_id"])
                await msg_obj.edit(embed=emb)
            except Exception:
                msg_obj = await ctx.send(embed=emb)
        else:
            msg_obj = await ctx.send(embed=emb)

        train_messages["east"][region] = msg_obj
        saved["east"][region] = {"channel_id": ctx.channel.id, "message_id": msg_obj.id}

    # 西日本
    for region, code in YAHOO_WEST_AREAS.items():
        data = fetch_area_info(region, code)
        emb = create_embed("JR西日本", region, data, 0x4682B4)

        entry = saved["west"].get(region)
        if entry:
            ch = bot.get_channel(entry["channel_id"])
            try:
                msg_obj = await ch.fetch_message(entry["message_id"])
                await msg_obj.edit(embed=emb)
            except Exception:
                msg_obj = await ctx.send(embed=emb)
        else:
            msg_obj = await ctx.send(embed=emb)

        train_messages["west"][region] = msg_obj
        saved["west"][region] = {"channel_id": ctx.channel.id, "message_id": msg_obj.id}

    save_message_ids(saved)

# ===== !運行情報更新 コマンド =====
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

# ===== on_ready =====
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    # 起動時に保存済みメッセージを読み込み、train_messages を再構築
    saved = load_message_ids()
    global REQUEST_CHANNEL
    # 先に東日本側のチャンネル情報を取得
    for region, entry in saved["east"].items():
        ch = bot.get_channel(entry["channel_id"])
        if ch:
            REQUEST_CHANNEL = ch
            break
    if REQUEST_CHANNEL is None:
        # 西日本でも同様
        for region, entry in saved["west"].items():
            ch = bot.get_channel(entry["channel_id"])
            if ch:
                REQUEST_CHANNEL = ch
                break

    # メッセージオブジェクトを取得
    for region, entry in saved["east"].items():
        ch = bot.get_channel(entry["channel_id"])
        if ch:
            try:
                msg_obj = await ch.fetch_message(entry["message_id"])
                train_messages["east"][region] = msg_obj
            except:
                pass
    for region, entry in saved["west"].items():
        ch = bot.get_channel(entry["channel_id"])
        if ch:
            try:
                msg_obj = await ch.fetch_message(entry["message_id"])
                train_messages["west"][region] = msg_obj
            except:
                pass

    # 再起動時に即時更新
    await update_train_info.callback()

    # ループ開始
    if not update_train_info.is_running():
        update_train_info.start()

if __name__ == "__main__":
    bot.run(TOKEN)
