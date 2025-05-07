import os
import logging
import time
from datetime import datetime
import discord
from discord.ext import commands, tasks
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
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
intents = discord.Intents.all()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 via Yahoo!路線情報 エリアコード
YAHOO_AREAS = {"関東": 4, "東北": 3, "中部": 5}

# JR西日本 API エリアコード→名前
JR_WEST_AREAS = {
    "hokuriku": "北陸", 
    "kinki": "近畿", 
    "chugoku": "中国", 
    "shikoku": "四国", 
    "kyushu": "九州"
}

# フィルタキーワード
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "遅延"]

# メッセージ保持用
# 東日本: 地域ごとの最新メッセージ
# 西日本: 地域ごとの最新メッセージ
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
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver

# ===== 補助関数 =====
def should_include(status: str, detail: str) -> bool:
    """
    運休、運転見合わせ、遅延のいずれかを含む場合にTrue
    """
    return any(kw in status for kw in DISRUPTION_KEYWORDS) or any(kw in detail for kw in DISRUPTION_KEYWORDS)

# --- JR東日本情報取得（Seleniumを使用） ---
def get_jr_east_filtered(region: str, area_code: int) -> list[dict]:
    """
    Seleniumを使用してJR東日本の運行情報を取得する
    リトライ機能と例外処理を強化
    """
    url = f"https://transit.yahoo.co.jp/traininfo/area/{area_code}/"
    driver = None
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            if driver is None:
                driver = create_driver()
            
            driver.get(url)
            
            # ページロード待機
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.linesWrap li"))
            )
            
            info = []
            
            # 路線情報を収集
            line_elements = driver.find_elements(By.CSS_SELECTOR, "ul.linesWrap li")
            for line_element in line_elements:
                try:
                    line_name = line_element.find_element(By.CSS_SELECTOR, ".labelLine").text.strip()
                    status = line_element.find_element(By.CSS_SELECTOR, ".statusTxt").text.strip()
                    
                    try:
                        detail_el = line_element.find_element(By.CSS_SELECTOR, ".statusDetail")
                        detail = detail_el.text.strip()
                    except:
                        detail = ""
                    
                    if should_include(status, detail):
                        info.append({
                            "路線名": f"[{region}] {line_name}",
                            "運行状況": status,
                            "詳細": detail or "詳細なし"
                        })
                except StaleElementReferenceException:
                    # 要素が古くなった場合、ループを再試行
                    logger.warning("StaleElementReferenceException発生。要素を再取得します。")
                    time.sleep(1)
                    continue
            
            if not info:
                info = [{"路線名": f"[{region}]", "運行状況": "現在問題ありません", "詳細": ""}]
            
            return info
            
        except TimeoutException:
            logger.warning(f"タイムアウト発生 (リトライ {retry_count+1}/{max_retries}): {url}")
            retry_count += 1
            
            # ドライバーをリセット
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None
            
            time.sleep(2)  # リトライ前に少し待機
                
        except Exception as e:
            logger.exception(f"Yahoo経由 JR東日本 {region} 取得失敗")
            return [{"路線名": f"[{region}] 取得失敗", "運行状況": "エラー", "詳細": str(e)}]
        
        finally:
            # 最終的にドライバーをクリーンアップ
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    # すべてのリトライが失敗
    return [{"路線名": f"[{region}] 取得失敗", "運行状況": "エラー", "詳細": "データ取得が繰り返し失敗しました"}]

# --- JR西日本情報取得（地域ごとに分割） ---
def get_jr_west_filtered(area_code: str) -> list[dict]:
    """
    指定された地域のJR西日本運行情報を取得
    """
    area_name = JR_WEST_AREAS.get(area_code, area_code)
    info = []
    
    try:
        jr = WestJR(area=area_code)
        traffic: TrainInfo = jr.get_traffic_info()
        
        # 在来線
        for route_code, li in traffic.lines.items():
            route_name = jr.lines.get(route_code, route_code)
            if should_include(li.status, li.cause or ""):
                info.append({
                    "路線コード": route_code,
                    "路線名": f"{route_name}",
                    "運行状況": li.status,
                    "詳細": li.cause or "詳細なし"
                })
        
        # 特急
        for express_code, ei in traffic.express.items():
            name = ei.name
            if should_include(ei.status, ei.cause or ""):
                info.append({
                    "路線コード": express_code,
                    "路線名": f"特急 {name}",
                    "運行状況": ei.status,
                    "詳細": ei.cause or "詳細なし"
                })
                
    except Exception as e:
        logger.exception(f"JR西日本 {area_name} 取得失敗")
        info.append({
            "路線名": f"取得失敗", 
            "運行状況": "エラー", 
            "詳細": str(e)
        })
    
    if not info:
        info = [{"路線名": "", "運行状況": "現在問題ありません", "詳細": ""}]
        
    return info

# --- 埋め込みメッセージ作成 ---
def create_east_embed(region: str, data: list[dict]) -> discord.Embed:
    """JR東日本の埋め込みメッセージを作成"""
    current_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    embed = discord.Embed(
        title=f"🚆 JR東日本（{region}） 運休・遅延情報", 
        color=0x2E8B57,
        description=f"最終更新: {current_time}"
    )
    
    for item in data:
        embed.add_field(
            name=f"{item['路線名']}：{item['運行状況']}",
            value=item['詳細'], 
            inline=False
        )
    
    return embed

def create_west_embed(area_code: str, data: list[dict]) -> discord.Embed:
    """JR西日本の埋め込みメッセージを作成"""
    area_name = JR_WEST_AREAS.get(area_code, area_code)
    current_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    embed = discord.Embed(
        title=f"🚆 JR西日本（{area_name}） 運休・遅延情報", 
        color=0x4682B4,
        description=f"最終更新: {current_time}"
    )
    
    for item in data:
        embed.add_field(
            name=f"{item['路線名']}：{item['運行状況']}",
            value=item['詳細'], 
            inline=False
        )
    
    return embed

# ===== タスク定義 =====
@tasks.loop(minutes=30)
async def update_train_info():
    """30分ごとに運行情報を更新するタスク"""
    if not TRAIN_INFO_CHANNEL_ID:
        return
    
    try:
        channel = bot.get_channel(int(TRAIN_INFO_CHANNEL_ID))
        if not channel:
            logger.error(f"チャンネル ID {TRAIN_INFO_CHANNEL_ID} が見つかりません")
            return
        
        logger.info("運行情報の自動更新を実行中...")
        
        # JR東日本の情報更新
        for region, code in YAHOO_AREAS.items():
            try:
                data = get_jr_east_filtered(region, code)
                embed = create_east_embed(region, data)
                
                # 既存のメッセージを更新、なければ新規作成
                if region in train_messages["east"]:
                    try:
                        await train_messages["east"][region].edit(embed=embed)
                        logger.info(f"JR東日本（{region}）情報を更新しました")
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        train_messages["east"][region] = msg
                        logger.info(f"JR東日本（{region}）情報を再作成しました")
                else:
                    msg = await channel.send(embed=embed)
                    train_messages["east"][region] = msg
                    logger.info(f"JR東日本（{region}）情報を新規作成しました")
            except Exception as e:
                logger.exception(f"JR東日本（{region}）情報の更新に失敗しました: {e}")
        
        # JR西日本の情報更新（地域別）
        for area_code in JR_WEST_AREAS.keys():
            try:
                area_name = JR_WEST_AREAS[area_code]
                data = get_jr_west_filtered(area_code)
                embed = create_west_embed(area_code, data)
                
                # 既存のメッセージを更新、なければ新規作成
                if area_code in train_messages["west"]:
                    try:
                        await train_messages["west"][area_code].edit(embed=embed)
                        logger.info(f"JR西日本（{area_name}）情報を更新しました")
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        train_messages["west"][area_code] = msg
                        logger.info(f"JR西日本（{area_name}）情報を再作成しました")
                else:
                    msg = await channel.send(embed=embed)
                    train_messages["west"][area_code] = msg
                    logger.info(f"JR西日本（{area_name}）情報を新規作成しました")
            except Exception as e:
                logger.exception(f"JR西日本（{area_name}）情報の更新に失敗しました: {e}")
                
    except Exception as e:
        logger.exception(f"運行情報の自動更新中にエラーが発生しました: {e}")

# ===== コマンド定義 =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    """
    コマンドで運行情報を取得・表示する
    """
    await ctx.send("🚅 運行情報を取得中です...")
    
    # JR東日本
    for region, code in YAHOO_AREAS.items():
        try:
            data = get_jr_east_filtered(region, code)
            embed = create_east_embed(region, data)
            msg = await ctx.send(embed=embed)
            train_messages["east"][region] = msg
        except Exception as e:
            logger.exception(f"JR東日本（{region}）情報の表示中にエラーが発生しました: {e}")
            await ctx.send(f"❌ JR東日本（{region}）情報の取得中にエラーが発生しました。")

    # JR西日本（地域別）
    for area_code in JR_WEST_AREAS.keys():
        try:
            area_name = JR_WEST_AREAS[area_code]
            data = get_jr_west_filtered(area_code)
            embed = create_west_embed(area_code, data)
            msg = await ctx.send(embed=embed)
            train_messages["west"][area_code] = msg
        except Exception as e:
            logger.exception(f"JR西日本（{area_name}）情報の表示中にエラーが発生しました: {e}")
            await ctx.send(f"❌ JR西日本（{area_name}）情報の取得中にエラーが発生しました。")

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    """
    コマンドで運行情報を手動更新する
    """
    await ctx.send("🔄 運行情報を更新中です...")
    
    try:
        # すべての情報を更新
        await update_train_info()
        await ctx.send("✅ 運行情報を更新しました！")
    except Exception as e:
        logger.exception(f"運行情報の手動更新中にエラーが発生しました: {e}")
        await ctx.send("❌ 運行情報の更新中にエラーが発生しました。")

# 起動時ログとタスク開始
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    
    # 自動更新タスク開始
    if not update_train_info.is_running():
        update_train_info.start()
        logger.info("運行情報の自動更新を開始しました")

    # 起動時の更新（即時実行）
    if TRAIN_INFO_CHANNEL_ID:
        await update_train_info()

bot.run(TOKEN)