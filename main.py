import os
import json
import logging
import traceback
import requests
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STATE_FILE = "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

YAHOO_EAST_AREAS = {"関東": 4, "東北": 3, "中部": 5}
YAHOO_WEST_AREAS = {"近畿": 6, "九州": 7, "中国": 8, "四国": 9}
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "列車遅延", "その他", "運転計画", "運行情報"]

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
            train_messages.update(state.get("messages", {}))
    except:
        pass

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "channel_id": REQUEST_CHANNEL,
                "messages": train_messages
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"state save failed: {e}")

# ===== ブロック処理スレッド化 =====
def _fetch_area_info_sync(region: str, code: int) -> list[dict]:
    base = "https://transit.yahoo.co.jp"
    url = f"{base}/diainfo/area/{code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for div in soup.select("div.elmTblLstLine"):
        tbl = div.find("table")
        if not tbl:
            continue
        for tr in tbl.select("tbody > tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            status = cols[1].get_text(strip=True)
            detail_preview = cols[2].get_text(strip=True)
            if not (any(kw in status for kw in DISRUPTION_KEYWORDS) or any(kw in detail_preview for kw in DISRUPTION_KEYWORDS)):
                continue
            a = cols[0].find("a", href=True)
            name = cols[0].get_text(strip=True)
            detail = detail_preview
            if a:
                link = base + a["href"]
                try:
                    lr = requests.get(link, headers=headers, timeout=15)
                    lr.raise_for_status()
                    lsoup = BeautifulSoup(lr.text, "html.parser")
                    h1 = lsoup.select_one("div.labelLarge h1.title")
                    name = h1.get_text(strip=True) if h1 else name
                    dd = lsoup.select_one("dd.trouble p")
                    detail = dd.get_text(strip=True) if dd else detail
                except:
                    pass
            items.append({"路線名": name, "運行状況": status, "詳細": detail})
    return items or [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]

async def fetch_area_info(region: str, code: int) -> list[dict]:
    return await asyncio.to_thread(_fetch_area_info_sync, region, code)

# ===== Embed分割送信 =====
async def send_paginated_embeds(prefix: str, region: str, data: list[dict], color: int, channel: discord.TextChannel):
    """データを25件ずつEmbedに分けて送信"""
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M")
    per_page = 25
    pages = (len(data) + per_page - 1) // per_page
    for i in range(pages):
        emb = discord.Embed(
            title=f"🚆 {prefix}（{region}） 運行情報 ({i+1}/{pages})",
            description=f"最終更新: {now}",
            color=color
        )
        start = i * per_page
        for entry in data[start:start+per_page]:
            name = f"{entry['路線名']}：{entry['運行状況']}"[:256]
            val  = (entry['詳細'] or "詳細なし")[:1024]
            emb.add_field(name=name, value=val, inline=False)
        await channel.send(embed=emb)

# ===== エラー通知 =====
async def send_error_report(ch, message, error):
    emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
    emb.add_field(name="詳細", value=f"```\n{error}\n```", inline=False)
    await ch.send(embed=emb)

# ===== 自動更新 =====
@tasks.loop(minutes=30)
async def update_train_info():
    global update_counter
    update_counter += 1
    if not REQUEST_CHANNEL:
        return
    ch = bot.get_channel(REQUEST_CHANNEL)
    if not ch:
        return

    # 東日本
    for region, code in YAHOO_EAST_AREAS.items():
        try:
            data = await fetch_area_info(region, code)
            await send_paginated_embeds("JR東日本", region, data, 0x2E8B57, ch)
        except Exception as e:
            await send_error_report(ch, f"JR東日本 {region} 更新失敗", e)

    # 西日本
    for region, code in YAHOO_WEST_AREAS.items():
        try:
            data = await fetch_area_info(region, code)
            await send_paginated_embeds("JR西日本", region, data, 0x4682B4, ch)
        except Exception as e:
            await send_error_report(ch, f"JR西日本 {region} 更新失敗", e)

    save_state()

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel.id
    save_state()

    # 東日本
    for region, code in YAHOO_EAST_AREAS.items():
        try:
            data = await fetch_area_info(region, code)
            await send_paginated_embeds("JR東日本", region, data, 0x2E8B57, ctx.channel)
        except Exception as e:
            await send_error_report(ctx, f"JR東日本 {region} 取得失敗", e)

    # 西日本
    for region, code in YAHOO_WEST_AREAS.items():
        try:
            data = await fetch_area_info(region, code)
            await send_paginated_embeds("JR西日本", region, data, 0x4682B4, ctx.channel)
        except Exception as e:
            await send_error_report(ctx, f"JR西日本 {region} 取得失敗", e)

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    try:
        await update_train_info()
        await ctx.send("✅ 更新完了！")
    except Exception as e:
        await send_error_report(ctx.channel, "手動更新失敗", e)

@bot.event
async def on_ready():
    load_state()
    if not update_train_info.is_running():
        update_train_info.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await send_error_report(ctx.channel, "コマンド実行エラー", error)

if __name__ == "__main__":
    bot.run(TOKEN)
