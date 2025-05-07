import configparser
import os
import logging
import sys
import time
import json
import requests
from datetime import datetime
import discord
from discord.ext import commands, tasks
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException
)
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
intents = discord.Intents.all()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_AREAS = {"関東": 4, "東北": 3, "中部": 5}

# JR西日本 路線情報
JR_WEST_LINES = {
    # 北陸エリア
    "hokuriku": {
        "name": "北陸",
        "lines": [
            {"id": "hokuriku", "name": "北陸本線"},
            {"id": "kosei", "name": "湖西線"},
            {"id": "oito", "name": "大糸線"}
        ]
    },
    # 近畿エリア
    "kinki": {
        "name": "近畿",
        "lines": [
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
        ]
    },
    # 中国エリア
    "chugoku": {
        "name": "中国",
        "lines": [
            {"id": "sanin", "name": "山陰本線"},
            {"id": "hakubi", "name": "伯備線"},
            {"id": "kabe", "name": "可部線"},
            {"id": "geibi", "name": "芸備線"},
            {"id": "sanyo", "name": "山陽本線"}
        ]
    },
    # 四国エリア
    "shikoku": {
        "name": "四国",
        "lines": [
            {"id": "yosan", "name": "予讃線"},
            {"id": "dosan", "name": "土讃線"},
            {"id": "kotoku", "name": "高徳線"},
            {"id": "naruto", "name": "鳴門線"},
            {"id": "tokushima", "name": "徳島線"}
        ]
    },
    # 九州エリア
    "kyushu": {
        "name": "九州",
        "lines": [
            {"id": "kagoshima", "name": "鹿児島本線"},
            {"id": "nippo", "name": "日豊本線"},
            {"id": "chikuhi", "name": "筑肥線"},
            {"id": "sasebo", "name": "佐世保線"},
            {"id": "nagasaki", "name": "長崎本線"},
            {"id": "hisatsu", "name": "肥薩線"}
        ]
    }
}

# フィルタキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# メッセージ保持用
train_messages = {
    "east": {},
    "west": {}
}

# Discord チャンネル ID（情報を自動投稿するチャンネル）
TRAIN_INFO_CHANNEL_ID = os.getenv("TRAIN_INFO_CHANNEL_ID")
if not TRAIN_INFO_CHANNEL_ID:
    logger.warning("TRAIN_INFO_CHANNEL_IDが設定されていません。自動投稿は行われません。")


# ===== Selenium設定 =====
def create_driver():
    """Seleniumドライバーを初期化する"""
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
    """
    運休、運転見合わせ、遅延のいずれかを含む場合にTrue
    """
    # 正常運転を示すパターン
    normal_patterns = [
        "平常", "通常", "現在も平常どおり", "平常どおり",
        "問題なく", "通り運転", "通常通り"
    ]
    is_normal = any(pat in status for pat in normal_patterns)
    return not is_normal or (detail and detail.strip() != "")


# --- JR東日本情報取得（Selenium使用） ---
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    """
    Seleniumを使用してJR東日本の運行情報を取得する
    リトライ機能と例外処理を強化
    """
    url = f"https://transit.yahoo.co.jp/traininfo/area/{area_code}/"
    max_retries = 5
    retry_count = 0
    driver = None
    
    while retry_count < max_retries:
        try:
            if driver is None:
                driver = create_driver()
            
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.linesWrap li"))
            )
            
            info = []
            line_elements = driver.find_elements(By.CSS_SELECTOR, "ul.linesWrap li")
            for line_el in line_elements:
                try:
                    line_name = line_el.find_element(By.CSS_SELECTOR, ".labelLine").text.strip()
                    status = line_el.find_element(By.CSS_SELECTOR, ".labelStatus").text.strip()
                    try:
                        detail = line_el.find_element(By.CSS_SELECTOR, ".trouble").text.strip()
                    except NoSuchElementException:
                        detail = ""
                    if should_include(status, detail):
                        info.append({
                            "路線名": line_name,
                            "運行状況": status,
                            "詳細": detail
                        })
                except Exception as e:
                    logger.warning(f"路線情報の解析中にエラー: {e}")
            return info
        
        except (TimeoutException, WebDriverException) as e:
            retry_count += 1
            logger.warning(f"{region}エリア読み込み失敗（{retry_count}/{max_retries}）: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None
        
        except Exception as e:
            logger.exception(f"JR東日本({region})情報取得中に予期せぬエラー: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            return [{
                "路線名": f"{region}エリア",
                "運行状況": "エラー",
                "詳細": f"データ取得エラー: {e}"
            }]
    
    # 最大リトライ超過
    if driver:
        try:
            driver.quit()
        except:
            pass
    return [{
        "路線名": f"{region}エリア",
        "運行状況": "エラー",
        "詳細": f"最大リトライ回数({max_retries}回)を超過しました"
    }]


# --- JR西日本情報取得 ---
def get_jr_west_filtered(area_code: str) -> list[dict]:
    """
    指定された地域のJR西日本運行情報を取得
    """
    area_info = JR_WEST_LINES.get(area_code)
    if not area_info:
        return [{
            "路線名": f"不明なエリア: {area_code}",
            "運行状況": "エラー",
            "詳細": "エリアコードが無効です"
        }]
    
    area_name = area_info["name"]
    info = []
    has_data = False
    
    for line in area_info["lines"]:
        line_id = line["id"]
        line_name = line["name"]
        try:
            url = f"https://www.train-guide.westjr.co.jp/api/v3/{line_id}.json"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"JR西日本 {line_name} API応答異常: {resp.status_code}")
                continue
            data = resp.json()
            has_data = True
            status = data.get("status", {}).get("text", "")
            detail = data.get("status", {}).get("detail", "")
            if should_include(status, detail):
                info.append({
                    "路線コード": line_id,
                    "路線名": line_name,
                    "運行状況": status,
                    "詳細": detail or "詳細なし"
                })
        except requests.exceptions.RequestException as e:
            logger.warning(f"JR西日本 {line_name} APIエラー: {e}")
            info.append({
                "路線コード": line_id,
                "路線名": line_name,
                "運行状況": "エラー",
                "詳細": f"データ取得失敗: {e}"
            })
        except Exception as e:
            logger.exception(f"JR西日本 {line_name} 処理エラー: {e}")
            info.append({
                "路線コード": line_id,
                "路線名": line_name,
                "運行状況": "エラー",
                "詳細": f"データ処理失敗: {e}"
            })
    
    if not info and has_data:
        return [{
            "路線名": f"{area_name}エリア全線",
            "運行状況": "現在問題ありません",
            "詳細": ""
        }]
    if not has_data:
        return [{
            "路線名": f"{area_name}エリア",
            "運行状況": "情報取得不可",
            "詳細": "いずれの路線からも情報を取得できませんでした"
        }]
    return info


# --- 埋め込みメッセージ作成 ---
def create_east_embed(region: str, data: list[dict]) -> discord.Embed:
    current_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    embed = discord.Embed(
        title=f"🚆 JR東日本（{region}） 運休・遅延情報",
        color=0x2E8B57,
        description=f"最終更新: {current_time}"
    )
    for item in data:
        embed.add_field(
            name=f"{item['路線名']}：{item['運行状況']}",
            value=item['詳細'], inline=False
        )
    return embed

def create_west_embed(area_code: str, data: list[dict]) -> discord.Embed:
    area_name = JR_WEST_LINES.get(area_code, {}).get("name", area_code)
    current_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    embed = discord.Embed(
        title=f"🚆 JR西日本（{area_name}） 運休・遅延情報",
        color=0x4682B4,
        description=f"最終更新: {current_time}"
    )
    for item in data:
        embed.add_field(
            name=f"{item.get('路線名', item.get('路線コード'))}：{item['運行状況']}",
            value=item['詳細'], inline=False
        )
    return embed


# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    if not TRAIN_INFO_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(int(TRAIN_INFO_CHANNEL_ID))
        if not channel:
            logger.error(f"チャンネルID {TRAIN_INFO_CHANNEL_ID} が見つかりません")
            return
        logger.info("運行情報の自動更新を実行中...")
        # 東日本
        for region, code in YAHOO_AREAS.items():
            try:
                data = get_jr_east_filtered(region, code)
                embed = create_east_embed(region, data)
                if region in train_messages["east"]:
                    try:
                        await train_messages["east"][region].edit(embed=embed)
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        train_messages["east"][region] = msg
                else:
                    msg = await channel.send(embed=embed)
                    train_messages["east"][region] = msg
            except Exception as e:
                logger.exception(f"JR東日本（{region}）更新失敗: {e}")
        # 西日本
        for area_code in JR_WEST_LINES.keys():
            try:
                data = get_jr_west_filtered(area_code)
                embed = create_west_embed(area_code, data)
                if area_code in train_messages["west"]:
                    try:
                        await train_messages["west"][area_code].edit(embed=embed)
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        train_messages["west"][area_code] = msg
                else:
                    msg = await channel.send(embed=embed)
                    train_messages["west"][area_code] = msg
            except Exception as e:
                logger.exception(f"JR西日本（{area_code}）更新失敗: {e}")
    except Exception as e:
        logger.exception(f"自動更新中にエラー: {e}")


# ===== スラッシュコマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    await ctx.send("🚅 運行情報を取得中です...")
    for region, code in YAHOO_AREAS.items():
        try:
            data = get_jr_east_filtered(region, code)
            embed = create_east_embed(region, data)
            msg = await ctx.send(embed=embed)
            train_messages["east"][region] = msg
        except Exception as e:
            logger.exception(f"JR東日本（{region}）表示エラー: {e}")
            await ctx.send(f"❌ JR東日本（{region}）情報取得失敗。")

    for area_code in JR_WEST_LINES.keys():
        try:
            data = get_jr_west_filtered(area_code)
            embed = create_west_embed(area_code, data)
            msg = await ctx.send(embed=embed)
            train_messages["west"][area_code] = msg
        except Exception as e:
            logger.exception(f"JR西日本（{area_code}）表示エラー: {e}")
            await ctx.send(f"❌ JR西日本（{area_code}）情報取得失敗。")


@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    await ctx.send("🔄 運行情報を更新中です...")
    try:
        await update_train_info()
        await ctx.send("✅ 運行情報を更新しました！")
    except Exception as e:
        logger.exception(f"手動更新中にエラー: {e}")
        await ctx.send("❌ 運行情報の更新中にエラーが発生しました。")


@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    if not update_train_info.is_running():
        update_train_info.start()
        logger.info("自動更新タスクを開始しました")
    if TRAIN_INFO_CHANNEL_ID:
        await update_train_info()


# ===== メイン =====
if __name__ == "__main__":
    config = configparser.ConfigParser()
    try:
        config.read("config.ini", encoding="utf-8")
        TOKEN = config.get("BOT", "TOKEN")
        TRAIN_INFO_CHANNEL_ID = config.get("BOT", "TRAIN_INFO_CHANNEL_ID", fallback=None)
    except Exception as e:
        logger.critical(f"設定ファイル読み込みエラー: {e}")
        sys.exit(1)

    train_messages = {"east": {}, "west": {}}
    bot.run(TOKEN)
