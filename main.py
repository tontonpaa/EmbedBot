import os
import re
import json
import logging
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

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_AREAS = {"関東": 4, "東北": 3, "中部": 5}

# JR西日本 路線情報
JR_WEST_LINES = {
    "hokuriku": {
        "name": "北陸",
        "lines": [
            {"id": "hokuriku", "name": "北陸本線"},
            {"id": "kosei",    "name": "湖西線"},
            {"id": "oito",     "name": "大糸線"},
        ]
    },
    "kinki": {
        "name": "近畿",
        "lines": [
            {"id": "kobesanyo","name": "JR神戸線・山陽本線"},
            {"id": "kyoto",    "name": "JR京都線・東海道本線"},
            {"id": "osaka",    "name": "大阪環状線"},
            {"id": "yamatoji", "name": "大和路線・関西本線"},
            {"id": "hanwahagoromo", "name": "阪和線・羽衣線"},
            {"id": "kansaiairport", "name": "関西空港線"},
            {"id": "tozai",    "name": "JR東西線"},
            {"id": "takarazuka","name": "JR宝塚線・福知山線"},
            {"id": "sakurai",  "name": "桜井線(万葉まほろば線)"},
            {"id": "nara",     "name": "奈良線"},
            {"id": "sagano",   "name": "嵯峨野線・山陰本線"},
            {"id": "kinokuni", "name": "きのくに線・紀勢本線"},
        ]
    },
    "chugoku": {
        "name": "中国",
        "lines": [
            {"id": "sanin", "name": "山陰本線"},
            {"id": "hakubi","name": "伯備線"},
            {"id": "kabe",  "name": "可部線"},
            {"id": "geibi", "name": "芸備線"},
            {"id": "sanyo", "name": "山陽本線"},
        ]
    },
    "shikoku": {
        "name": "四国",
        "lines": [
            {"id": "yosan",     "name": "予讃線"},
            {"id": "dosan",     "name": "土讃線"},
            {"id": "kotoku",    "name": "高徳線"},
            {"id": "naruto",    "name": "鳴門線"},
            {"id": "tokushima", "name": "徳島線"},
        ]
    },
    "kyushu": {
        "name": "九州",
        "lines": [
            {"id": "kagoshima","name": "鹿児島本線"},
            {"id": "nippo",    "name": "日豊本線"},
            {"id": "chikuhi",  "name": "筑肥線"},
            {"id": "sasebo",   "name": "佐世保線"},
            {"id": "nagasaki", "name": "長崎本線"},
            {"id": "hisatsu",  "name": "肥薩線"},
        ]
    }
}

DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# メッセージ保持用・自動投稿チャンネル
train_messages = {"east": {}, "west": {}}
REQUEST_CHANNEL = None

# ===== 補助関数 =====
def should_include(status: str, detail: str) -> bool:
    normal_patterns = ["平常", "通常", "問題なく", "通常通り"]
    return not any(p in status for p in normal_patterns) or bool(detail and detail.strip())

def find_train_info(data) -> list:
    """
    再帰的に data を検索して、
    'lineName' や 'statusText' を含む辞書のリストを返す。
    """
    candidates = []

    def recurse(obj):
        if isinstance(obj, list):
            if obj and all(isinstance(item, dict) for item in obj):
                # 要素に lineName または statusText があるリストを候補とする
                if any('lineName' in item or 'statusText' in item for item in obj):
                    candidates.append(obj)
                    return
            for item in obj:
                recurse(item)
        elif isinstance(obj, dict):
            for v in obj.values():
                recurse(v)

    recurse(data)
    if not candidates:
        return None
    # 最長のリストを選択
    return max(candidates, key=lambda lst: len(lst))

# --- JR東日本情報取得（__NEXT_DATA__ → JSON API版） ---
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    page_url = f"https://transit.yahoo.co.jp/diainfo/area/{area_code}"
    headers = {"User-Agent": "Mozilla/5.0"}

    # 1) ページHTMLを取得して __NEXT_DATA__ を抜き出す
    try:
        resp = requests.get(page_url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"JR東日本 {region} ページ取得エラー: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": str(e)}]

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        resp.text, re.DOTALL
    )
    if not m:
        logger.error(f"JR東日本 {region} __NEXT_DATA__ が見つかりません")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": "__NEXT_DATA__ 抽出失敗"}]
    try:
        next_data = json.loads(m.group(1))
        build_id = next_data["buildId"]
    except Exception as e:
        logger.error(f"JR東日本 {region} buildId 抽出失敗: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": "buildId 抽出エラー"}]

    # 2) JSON API を叩く
    json_url = f"https://transit.yahoo.co.jp/_next/data/{build_id}/diainfo/area/{area_code}.json"
    try:
        jres = requests.get(json_url, headers=headers, timeout=15)
        jres.raise_for_status()
        data = jres.json()
    except Exception as e:
        logger.error(f"JR東日本 {region} JSON API エラー: {e}")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": "JSON API 取得失敗"}]

    # 3) JSON から運行情報を取り出す
    info_list = find_train_info(data)
    if not info_list:
        logger.warning(f"JR東日本 {region} データキー未検出")
        return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": "データ構造が不明"}]

    # 4) 整形して返す
    items = []
    for entry in info_list:
        name   = entry.get("lineName") or entry.get("name")
        status = entry.get("statusText") or entry.get("status")
        detail = entry.get("detail") or entry.get("description") or ""
        if name and status and should_include(status, detail):
            items.append({"路線名": name, "運行状況": status, "詳細": detail})
    if not items:
        return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]
    return items

# --- JR西日本情報取得 ---
def get_jr_west_filtered(area_code: str) -> list[dict]:
    area = JR_WEST_LINES.get(area_code)
    if not area:
        return [{"路線名": f"不明エリア {area_code}", "運行状況": "エラー", "詳細": "無効なエリアコード"}]

    items = []
    has_data = False
    for ln in area["lines"]:
        lid, lname = ln["id"], ln["name"]
        retries, max_retries = 0, 3
        while retries < max_retries:
            try:
                resp = requests.get(
                    f"https://www.train-guide.westjr.co.jp/api/v3/{lid}.json",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15
                )
                if resp.status_code == 200:
                    has_data = True
                    data = resp.json()
                    status = data.get("status", {}).get("text", "")
                    detail = data.get("status", {}).get("detail", "")
                    if should_include(status, detail):
                        items.append({"路線名": lname, "運行状況": status, "詳細": detail or "詳細なし"})
                    break
                elif resp.status_code == 404:
                    has_data = True
                    break
                else:
                    retries += 1
                    time.sleep(1)
            except requests.RequestException as e:
                retries += 1
                if retries >= max_retries:
                    items.append({"路線名": lname, "運行状況": "エラー", "詳細": str(e)})
                else:
                    time.sleep(1)

    if not items and has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "平常運転", "詳細": ""}]
    if not has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "情報取得不可", "詳細": "取得失敗"}]
    return items

# --- Embed作成 ---
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
    area_name = JR_WEST_LINES.get(area_code, {}).get("name", area_code)
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(
        title=f"🚆 JR西日本（{area_name}） 運行情報",
        description=f"最終更新: {now}",
        color=0x4682B4
    )
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}", value=x['詳細'] or "詳細なし", inline=False)
    return emb

# --- エラーレポート ---
async def send_error_report(ch, message, error):
    try:
        emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
        emb.add_field(name="詳細", value=f"```\n{error}\n```", inline=False)
        await ch.send(embed=emb)
    except:
        logger.error("エラーレポート送信失敗")

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
            await send_error_report(ch, f"JR東日本 {reg} 自動更新中にエラー", e)
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
            await send_error_report(ch, f"JR西日本 {area} 自動更新中にエラー", e)

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel
    # 東日本
    for reg, code in YAHOO_AREAS.items():
        try:
            data = get_jr_east_filtered(reg, code)
            emb  = create_east_embed(reg, data)
            msg  = await ctx.send(embed=emb)
            train_messages["east"][reg] = msg
        except Exception as e:
            logger.exception(f"コマンド JR東日本 {reg} エラー")
            await ctx.send(f"⚠️ JR東日本（{reg}）情報取得エラー")
    # 西日本
    for area in JR_WEST_LINES.keys():
        try:
            data = get_jr_west_filtered(area)
            emb  = create_west_embed(area, data)
            msg  = await ctx.send(embed=emb)
            train_messages["west"][area] = msg
        except Exception as e:
            logger.exception(f"コマンド JR西日本 {area} エラー")
            await ctx.send(f"⚠️ JR西日本（{JR_WEST_LINES[area]['name']}）情報取得エラー")

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    msg = await ctx.send("🔄 更新中…")
    try:
        await update_train_info()
        await msg.edit(content="✅ 更新完了！")
    except Exception as e:
        logger.exception("手動更新エラー")
        await msg.edit(content="❌ 更新失敗")
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
