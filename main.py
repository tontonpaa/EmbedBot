import os
import re
import json
import logging
import socket
from datetime import datetime
import time
import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Environment variable DISCORD_TOKEN is not set.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot 初期化
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 定数定義 ---
YAHOO_AREAS = {"関東": 4, "東北": 3, "中部": 5}
JR_WEST_LINES = {
    # ...（省略せずに全エリアを入れてください）...
    "hokuriku": {"name":"北陸","lines":[{"id":"hokuriku","name":"北陸本線"},{"id":"kosei","name":"湖西線"},{"id":"oito","name":"大糸線"}]},
    # 略
}
DISRUPTION_KEYWORDS = ["運休","運転見合わせ","遅延"]

train_messages = {"east":{}, "west":{}}
REQUEST_CHANNEL = None

# ===== ヘルパー =====
def should_include(status: str, detail: str) -> bool:
    normal = ["平常","通常","問題なく","通常通り"]
    return not any(p in status for p in normal) or bool(detail and detail.strip())

def find_train_info(data) -> list[dict] | None:
    """再帰的にJSONを探索し、lineName/statusTextを含む辞書のリストを返す"""
    candidates = []
    def recurse(obj):
        if isinstance(obj, list):
            dicts = [x for x in obj if isinstance(x, dict)]
            matches = [d for d in dicts if 'lineName' in d and 'statusText' in d]
            if len(matches) >= max(1, len(dicts)//2):
                candidates.append(matches)
            for x in obj:
                recurse(x)
        elif isinstance(obj, dict):
            for v in obj.values():
                recurse(v)
    recurse(data)
    return max(candidates, key=len) if candidates else None

# ===== JR東日本取得 =====
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    """
    1) HTMLから__NEXT_DATA__を抽出
    2) buildIdからJSON APIを叩く
    3) find_train_infoでリストを取り出し整形
    """
    page_url = f"https://transit.yahoo.co.jp/diainfo/area/{area_code}"
    headers = {"User-Agent":"Mozilla/5.0"}
    # (1) ページ取得
    try:
        resp = requests.get(page_url, headers=headers, timeout=15)
        resp.raise_for_status()
    except (requests.RequestException, socket.gaierror) as e:
        logger.error(f"JR東日本 {region} ページ取得失敗: {e}")
        return [{"路線名":f"{region}エリア","運行状況":"エラー","詳細":str(e)}]

    # (2) __NEXT_DATA__ 抽出
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>([\s\S]+?)</script>', resp.text)
    if not m:
        logger.error(f"JR東日本 {region} __NEXT_DATA__ 抽出失敗")
        return [{"路線名":f"{region}エリア","運行状況":"エラー","詳細":"__NEXT_DATA__が見つかりません"}]
    try:
        next_data = json.loads(m.group(1))
        build_id  = next_data["buildId"]
    except Exception as e:
        logger.error(f"JR東日本 {region} buildId 抽出失敗: {e}")
        return [{"路線名":f"{region}エリア","運行状況":"エラー","詳細":"buildId抽出エラー"}]

    # (3) JSON API取得
    json_url = f"https://transit.yahoo.co.jp/_next/data/{build_id}/diainfo/area/{area_code}.json"
    try:
        jres = requests.get(json_url, headers=headers, timeout=15)
        jres.raise_for_status()
        data = jres.json()
    except (requests.RequestException, socket.gaierror) as e:
        logger.error(f"JR東日本 {region} JSON取得失敗: {e}")
        return [{"路線名":f"{region}エリア","運行状況":"エラー","詳細":"JSON API取得失敗"}]

    # (4) 情報抽出
    info_list = find_train_info(data)
    if not info_list:
        logger.warning(f"JR東日本 {region} データ構造が不明")
        return [{"路線名":f"{region}エリア","運行状況":"エラー","詳細":"データキー未検出"}]

    # (5) 整形
    items = []
    for e in info_list:
        name   = e.get("lineName")   or e.get("name")
        status = e.get("statusText") or e.get("status")
        detail = e.get("detail")     or e.get("description","")
        if name and status and should_include(status, detail):
            items.append({"路線名":name,"運行状況":status,"詳細":detail})
    if not items:
        return [{"路線名":f"{region}全線","運行状況":"平常運転","詳細":""}]
    return items

# ===== JR西日本取得 =====
def get_jr_west_filtered(area_code: str) -> list[dict]:
    area = JR_WEST_LINES.get(area_code)
    if not area:
        return [{"路線名":f"不明エリア {area_code}","運行状況":"エラー","詳細":"無効なエリアコード"}]

    items, has_data = [], False
    for ln in area["lines"]:
        lid, lname = ln["id"], ln["name"]
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"https://www.train-guide.westjr.co.jp/api/v3/{lid}.json",
                    headers={"User-Agent":"Mozilla/5.0"},
                    timeout=15
                )
                if resp.status_code == 200:
                    has_data = True
                    d = resp.json()
                    st = d.get("status",{}).get("text","")
                    dt = d.get("status",{}).get("detail","")
                    if should_include(st, dt):
                        items.append({"路線名":lname,"運行状況":st,"詳細":dt or "詳細なし"})
                    break
                elif resp.status_code == 404:
                    has_data = True
                    break
            except (requests.RequestException, socket.gaierror) as e:
                last_err = e
                time.sleep(1)
        else:
            # 3回失敗
            items.append({"路線名":lname,"運行状況":"エラー","詳細":str(last_err)})

    if not items and has_data:
        return [{"路線名":f"{area['name']}全線","運行状況":"平常運転","詳細":""}]
    if not has_data:
        return [{"路線名":f"{area['name']}全線","運行状況":"情報取得不可","詳細":"全路線で取得失敗"}]
    return items

# ===== Embed作成 =====
def create_east_embed(region: str, data: list[dict]) -> discord.Embed:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(
        title=f"🚆 JR東日本（{region}） 運行情報",
        description=f"最終更新: {now}",
        color=0x2E8B57
    )
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}", value=x['詳細'] or "詳細なし", inline=False)
    return emb

def create_west_embed(area_code: str, data: list[dict]) -> discord.Embed:
    area_name = JR_WEST_LINES.get(area_code,{}).get("name", area_code)
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(
        title=f"🚆 JR西日本（{area_name}） 運行情報",
        description=f"最終更新: {now}",
        color=0x4682B4
    )
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}", value=x['詳細'] or "詳細なし", inline=False)
    return emb

# ===== エラーレポート =====
async def send_error_report(ch, message, error):
    try:
        emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
        emb.add_field(name="詳細", value=f"```\n{error}\n```", inline=False)
        await ch.send(embed=emb)
    except:
        logger.error("エラーレポート送信に失敗しました")

# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global REQUEST_CHANNEL
    if not REQUEST_CHANNEL:
        return
    ch = REQUEST_CHANNEL

    # 東日本
    for reg, code in YAHOO_AREAS.items():
        try:
            data = get_jr_east_filtered(reg, code)
            emb  = create_east_embed(reg, data)
            if reg in train_messages["east"]:
                try:
                    await train_messages["east"][reg].edit(embed=emb)
                except discord.NotFound:
                    msg = await ch.send(embed=emb)
                    train_messages["east"][reg] = msg
            else:
                msg = await ch.send(embed=emb)
                train_messages["east"][reg] = msg
        except Exception as e:
            logger.exception(f"自動更新 JR東日本 {reg} エラー")
            await send_error_report(ch, f"JR東日本 {reg} 自動更新中にエラー発生", e)

    # 西日本
    for area in JR_WEST_LINES.keys():
        try:
            data = get_jr_west_filtered(area)
            emb  = create_west_embed(area, data)
            if area in train_messages["west"]:
                try:
                    await train_messages["west"][area].edit(embed=emb)
                except discord.NotFound:
                    msg = await ch.send(embed=emb)
                    train_messages["west"][area] = msg
            else:
                msg = await ch.send(embed=emb)
                train_messages["west"][area] = msg
        except Exception as e:
            logger.exception(f"自動更新 JR西日本 {area} エラー")
            await send_error_report(ch, f"JR西日本 {area} 自動更新中にエラー発生", e)

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel

    for reg, code in YAHOO_AREAS.items():
        data = get_jr_east_filtered(reg, code)
        emb  = create_east_embed(reg, data)
        msg  = await ctx.send(embed=emb)
        train_messages["east"][reg] = msg

    for area in JR_WEST_LINES.keys():
        data = get_jr_west_filtered(area)
        emb  = create_west_embed(area, data)
        msg  = await ctx.send(embed=emb)
        train_messages["west"][area] = msg

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    status = await ctx.send("🔄 手動更新中...")
    try:
        await update_train_info()
        await status.edit(content="✅ 更新完了！")
    except Exception as e:
        logger.exception("手動更新エラー")
        await status.edit(content="❌ 更新失敗")
        await send_error_report(ctx.channel, "手動更新エラー", e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.exception("コマンド処理エラー")
    await send_error_report(ctx.channel, "コマンド実行中にエラー発生", error)

@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    if not update_train_info.is_running():
        update_train_info.start()

if __name__ == "__main__":
    bot.run(TOKEN)
