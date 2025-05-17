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
from typing import list

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STATE_FILE = "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.presence_task_started = False
target_channels: list[discord.TextChannel]
bot.target_channels = []

YAHOO_EAST_AREAS = {"関東": 4, "東北": 3, "中部": 5}
YAHOO_WEST_AREAS = {"近畿": 6, "九州": 7, "中国": 8, "四国": 9}
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "列車遅延", "その他", "運転計画", "運行情報"]

# ===== ブロック処理スレッド化 =====
def _fetch_area_info_sync(region: str, code: int) -> list[dict]:
    base = "https://transit.yahoo.co.jp"
    url = f"{base}/diainfo/area/{code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[dict] = []

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
            if not (any(kw in status for kw in DISRUPTION_KEYWORDS)
                    or any(kw in detail_preview for kw in DISRUPTION_KEYWORDS)):
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
                except Exception:
                    pass

            items.append({
                "路線名": name,
                "運行状況": status,
                "詳細": detail
            })

    if not items:
        return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]
    return items

async def fetch_area_info(region: str, code: int) -> list[dict]:
    return await asyncio.to_thread(_fetch_area_info_sync, region, code)

# ===== Embed 分割送信 =====
async def send_paginated_embeds(prefix: str, region: str, data: list[dict], color: int, channel: discord.TextChannel):
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
        for entry in data[start:start + per_page]:
            name = f"{entry['路線名']}：{entry['運行状況']}"[:256]
            val = (entry['詳細'] or "詳細なし")[:1024]
            emb.add_field(name=name, value=val, inline=False)
        await channel.send(embed=emb)

# ===== エラー通知 =====
async def send_error_report(ch: discord.TextChannel, message: str, error: Exception):
    emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
    emb.add_field(name="詳細", value=f"```\n{str(error)}\n```", inline=False)
    await ch.send(embed=emb)

# ===== 自動更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    logger.info("自動更新開始")
    for ch in bot.target_channels:
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
    logger.info("自動更新完了")

@update_train_info.error
async def update_error(error):
    logger.error(f"自動更新タスクでエラー: {error}")
    traceback.print_exc()

# ===== イベント =====
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    await bot.wait_until_ready()

    # 「運行情報」を含むチャンネルを全ギルドから収集
    bot.target_channels.clear()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if "運行情報" in channel.name and channel.permissions_for(guild.me).send_messages:
                bot.target_channels.append(channel)

    if not bot.target_channels:
        logger.warning("運行情報チャンネルが見つかりませんでした。")
    else:
        # 起動時に一度送信
        await update_train_info()
        # 30分ごとの自動更新開始
        if not update_train_info.is_running():
            update_train_info.start()

    if not bot.presence_task_started:
        bot.loop.create_task(update_presence())
        bot.presence_task_started = True

async def update_presence():
    await bot.wait_until_ready()
    while True:
        try:
            ping = round(bot.latency * 1000)
            await bot.change_presence(activity=discord.Game(name=f"Ping: {ping}ms"))
            await asyncio.sleep(5)
            await bot.change_presence(activity=discord.Game(name=f"サーバー数: {len(bot.guilds)}"))
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"[update_presence エラー] {e}")
            await asyncio.sleep(10)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await send_error_report(ctx.channel, "コマンド実行エラー", error)

# ===== 手動コマンド =====
@bot.command(name="運行情報")
async def manual_info(ctx: commands.Context):
    for channel in bot.target_channels:
        for region, code in YAHOO_EAST_AREAS.items():
            try:
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR東日本", region, data, 0x2E8B57, channel)
            except Exception as e:
                await send_error_report(channel, f"手動 JR東日本 {region} 取得失敗", e)
        for region, code in YAHOO_WEST_AREAS.items():
            try:
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR西日本", region, data, 0x4682B4, channel)
            except Exception as e:
                await send_error_report(channel, f"手動 JR西日本 {region} 取得失敗", e)

if __name__ == "__main__":
    bot.run(TOKEN)
