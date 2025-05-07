import os
import logging
from datetime import datetime
import requests
import discord
from discord.ext import commands, tasks
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
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
    "hokuriku": {"name": "北陸", "lines": [
        {"id": "hokuriku", "name": "北陸本線"},
        {"id": "kosei", "name": "湖西線"},
        {"id": "oito", "name": "大糸線"}
    ]},
    "kinki": {"name": "近畿", "lines": [
        {"id": "kobesanyo", "name": "JR神戸線・山陽本線"},
        {"id": "kyoto", "name": "JR京都線・東海道本線"},
        {"id": "osaka", "name": "大阪環状線"},
        {"id": "yamatoji", "name": "大和路線・関西本線"},
        {"id": "hanwahagoromo", "name": "阪和線・羽衣線"},
        {"id": "kansaiairport", "name": "関西空港線"},
        {"id": "tozai", "name": "JR東西線"},
        {"id": "takarazuka", "name": "JR宝塚線・福知山線"},
        {"id": "sakurai", "name": "桜井線(万葉まほろば線)"},
        {"id": "nara", "name": "奈良線"},
        {"id": "sagano", "name": "嵯峨野線・山陰本線"},
        {"id": "kinokuni", "name": "きのくに線・紀勢本線"}
    ]},
    "chugoku": {"name": "中国", "lines": [
        {"id": "sanin", "name": "山陰本線"},
        {"id": "hakubi", "name": "伯備線"},
        {"id": "kabe", "name": "可部線"},
        {"id": "geibi", "name": "芸備線"},
        {"id": "sanyo", "name": "山陽本線"}
    ]},
    "shikoku": {"name": "四国", "lines": [
        {"id": "yosan", "name": "予讃線"},
        {"id": "dosan", "name": "土讃線"},
        {"id": "kotoku", "name": "高徳線"},
        {"id": "naruto", "name": "鳴門線"},
        {"id": "tokushima", "name": "徳島線"}
    ]},
    "kyushu": {"name": "九州", "lines": [
        {"id": "kagoshima", "name": "鹿児島本線"},
        {"id": "nippo", "name": "日豊本線"},
        {"id": "chikuhi", "name": "筑肥線"},
        {"id": "sasebo", "name": "佐世保線"},
        {"id": "nagasaki", "name": "長崎本線"},
        {"id": "hisatsu", "name": "肥薩線"}
    ]}
}

# フィルタキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# メッセージ保持用
train_messages = {"east": {}, "west": {}}
# 自動投稿先チャンネル（コマンド実行時にセット）
REQUEST_CHANNEL = None

# ===== Selenium設定 =====
def create_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver

# ===== 補助関数 =====
def should_include(status: str, detail: str) -> bool:
    normal_patterns = ["平常", "通常", "現在も平常どおり", "平常どおり", "問題なく", "通り運転", "通常通り"]
    return not any(p in status for p in normal_patterns) or bool(detail and detail.strip())

# --- JR東日本情報取得 ---
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    url = f"https://transit.yahoo.co.jp/traininfo/area/{area_code}/"
    retries, max_retries = 0, 5
    driver = None
    while retries < max_retries:
        try:
            if not driver:
                driver = create_driver()
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.linesWrap li"))
            )
            items = []
            for el in driver.find_elements(By.CSS_SELECTOR, "ul.linesWrap li"):
                try:
                    name = el.find_element(By.CSS_SELECTOR, ".labelLine").text.strip()
                    status = el.find_element(By.CSS_SELECTOR, ".labelStatus").text.strip()
                    detail = el.find_element(By.CSS_SELECTOR, ".trouble").text.strip() if el.find_elements(By.CSS_SELECTOR, ".trouble") else ""
                    if should_include(status, detail):
                        items.append({"路線名": name, "運行状況": status, "詳細": detail})
                except Exception as ex:
                    logger.warning(f"JR東日本解析エラー: {ex}")
            return items
        except (TimeoutException, WebDriverException) as ex:
            retries += 1
            logger.warning(f"JR東日本 {region} 読込失敗 ({retries}/{max_retries}): {ex}")
            if driver:
                try: driver.quit()
                except: pass
                driver = None
        except Exception as ex:
            logger.exception(f"JR東日本 {region} 予期せぬエラー: {ex}")
            if driver:
                try: driver.quit()
                except: pass
            return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": str(ex)}]
    if driver:
        try: driver.quit()
        except: pass
    return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": "最大リトライ超過"}]

# --- JR西日本情報取得 ---
def get_jr_west_filtered(area_code: str) -> list[dict]:
    area = JR_WEST_LINES.get(area_code)
    if not area:
        return [{"路線名": f"不明エリア {area_code}", "運行状況": "エラー", "詳細": "無効なエリアコード"}]
    items, has_data = [], False
    for ln in area["lines"]:
        lid, lname = ln["id"], ln["name"]
        try:
            resp = requests.get(f"https://www.train-guide.westjr.co.jp/api/v3/{lid}.json", timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            has_data = True
            status = data.get("status", {}).get("text", "")
            detail = data.get("status", {}).get("detail", "")
            if should_include(status, detail):
                items.append({"路線名": lname, "運行状況": status, "詳細": detail or "詳細なし"})
        except Exception as ex:
            logger.warning(f"JR西日本 {lname} エラー: {ex}")
            items.append({"路線名": lname, "運行状況": "エラー", "詳細": str(ex)})
    if not items and has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "問題ありません", "詳細": ""}]
    if not has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "情報取得不可", "詳細": "取得失敗"}]
    return items

# --- Embed作成 ---
def create_east_embed(region: str, data: list[dict]) -> discord.Embed:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", description=f"最終更新: {now}", color=0x2E8B57)
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}", value=x['詳細'], inline=False)
    return emb


def create_west_embed(area_code: str, data: list[dict]) -> discord.Embed:
    name = JR_WEST_LINES.get(area_code, {}).get("name", area_code)
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(title=f"🚆 JR西日本（{name}）運行情報", description=f"最終更新: {now}", color=0x4682B4)
    for x in data:
        emb.add_field(name=f"{x['路線名']}：{x['運行状況']}", value=x['詳細'], inline=False)
    return emb

# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global REQUEST_CHANNEL
    if not REQUEST_CHANNEL:
        return
    ch = REQUEST_CHANNEL
    logger.info(f"自動更新投稿: チャンネル {ch.id}")
    for reg, code in YAHOO_AREAS.items():
        data = get_jr_east_filtered(reg, code)
        emb = create_east_embed(reg, data)
        if reg in train_messages['east']:
            try:
                await train_messages['east'][reg].edit(embed=emb)
            except discord.NotFound:
                m = await ch.send(embed=emb)
                train_messages['east'][reg] = m
        else:
            m = await ch.send(embed=emb)
            train_messages['east'][reg] = m
    for area in JR_WEST_LINES.keys():
        data = get_jr_west_filtered(area)
        emb = create_west_embed(area, data)
        if area in train_messages['west']:
            try:
                await train_messages['west'][area].edit(embed=emb)
            except discord.NotFound:
                m = await ch.send(embed=emb)
                train_messages['west'][area] = m
        else:
            m = await ch.send(embed=emb)
            train_messages['west'][area] = m

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel
    await ctx.send("🚅 運行情報を取得中です...")
    for reg, code in YAHOO_AREAS.items():
        data = get_jr_east_filtered(reg, code)
        emb = create_east_embed(reg, data)
        msg = await ctx.send(embed=emb)
        train_messages['east'][reg] = msg
    for area in JR_WEST_LINES.keys():
        data = get_jr_west_filtered(area)
        emb = create_west_embed(area, data)
        msg = await ctx.send(embed=emb)
        train_messages['west'][area] = msg

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    await ctx.send("🔄 手動更新中...")
    await update_train_info()
    await ctx.send("✅ 更新完了しました！")

# ===== 起動時イベント =====
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    if not update_train_info.is_running():
        update_train_info.start()

# ===== エントリポイント =====
if __name__ == "__main__":
    bot.run(TOKEN)