import os
import logging
from datetime import datetime
import time
import requests
import discord
from discord.ext import commands, tasks
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException
from dotenv import load_dotenv

# ===== 設定 =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Environment variable DISCORD_TOKEN is not set.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot 初期化
intents = discord.Intents.all()
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
    # UA更新: 最新のChromeバージョンを使用
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # メモリリーク防止策
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-in-process-stack-traces")
    
    # 修正: ウェブドライバの新しいバージョンでの安定性向上
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)  # タイムアウト延長
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
            
            logger.info(f"JR東日本 {region} ページ読み込み開始")
            driver.get(url)
            
            # 複数のセレクタを試す (Yahoo!のページレイアウト変更に対応)
            selectors = [
                "ul.linesWrap li", 
                ".line-list li",     # 代替セレクタ1
                ".area-info-item"    # 代替セレクタ2
            ]
            
            # いずれかのセレクタが見つかるまで待機
            element_found = False
            for selector in selectors:
                try:
                    logger.info(f"セレクタ試行中: {selector}")
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    element_found = True
                    active_selector = selector
                    logger.info(f"セレクタ成功: {selector}")
                    break
                except (TimeoutException, WebDriverException):
                    logger.warning(f"セレクタ失敗: {selector}")
                    continue
            
            if not element_found:
                logger.warning(f"JR東日本 {region} すべてのセレクタ失敗")
                raise TimeoutException("すべてのセレクタが見つかりませんでした")
            
            # ページ完全読み込み待機
            time.sleep(2)
            
            # 情報取得
            items = []
            elements = driver.find_elements(By.CSS_SELECTOR, active_selector)
            logger.info(f"取得要素数: {len(elements)}")
            
            for el in elements:
                try:
                    # 複数のセレクタパターンを試す
                    name = None
                    for name_selector in [".labelLine", ".line-name", ".name"]:
                        try:
                            name_elements = el.find_elements(By.CSS_SELECTOR, name_selector)
                            if name_elements:
                                name = name_elements[0].text.strip()
                                break
                        except:
                            continue
                    
                    status = None
                    for status_selector in [".labelStatus", ".status", ".condition"]:
                        try:
                            status_elements = el.find_elements(By.CSS_SELECTOR, status_selector)
                            if status_elements:
                                status = status_elements[0].text.strip()
                                break
                        except:
                            continue
                    
                    detail = ""
                    for detail_selector in [".trouble", ".detail", ".information"]:
                        try:
                            detail_elements = el.find_elements(By.CSS_SELECTOR, detail_selector)
                            if detail_elements:
                                detail = detail_elements[0].text.strip()
                                break
                        except:
                            continue
                    
                    if name and status:
                        if should_include(status, detail):
                            items.append({"路線名": name, "運行状況": status, "詳細": detail})
                except StaleElementReferenceException:
                    logger.warning("要素参照の状態が変化しました。リトライします。")
                    break  # 外側のループでリトライ
                except Exception as ex:
                    logger.warning(f"JR東日本解析エラー: {ex}")
            
            # 要素が正常に処理できた場合
            if items:
                logger.info(f"JR東日本 {region} 成功: {len(items)}件の情報を取得")
                if driver:
                    try: driver.quit()
                    except: pass
                return items
            elif retries < max_retries - 1:
                logger.warning(f"JR東日本 {region} 要素取得できず。リトライ中 ({retries+1}/{max_retries})")
                retries += 1
                if driver:
                    try: driver.quit()
                    except: pass
                    driver = None
                time.sleep(2)  # リトライ前に少し待機
            else:
                # 最終リトライでも空だった場合は正常と判断
                logger.info(f"JR東日本 {region} 最終チェック: 情報なし（正常運行と判断）")
                if driver:
                    try: driver.quit()
                    except: pass
                return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": ""}]
                
        except (TimeoutException, WebDriverException) as ex:
            retries += 1
            logger.warning(f"JR東日本 {region} 読込失敗 ({retries}/{max_retries}): {type(ex).__name__}: {ex}")
            if driver:
                try: driver.quit()
                except: pass
                driver = None
            time.sleep(3)  # リトライ前に待機時間を追加
            
        except Exception as ex:
            logger.exception(f"JR東日本 {region} 予期せぬエラー: {ex}")
            if driver:
                try: driver.quit()
                except: pass
            return [{"路線名": f"{region}エリア", "運行状況": "エラー", "詳細": f"{type(ex).__name__}: {str(ex)}"}]
    
    # すべてのリトライが失敗した場合
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
            # リクエストタイムアウトと再試行処理を追加
            retries = 0
            max_retries = 3
            while retries < max_retries:
                try:
                    resp = requests.get(
                        f"https://www.train-guide.westjr.co.jp/api/v3/{lid}.json", 
                        timeout=15,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        }
                    )
                    if resp.status_code == 200:
                        break
                    retries += 1
                    if retries < max_retries:
                        time.sleep(1)  # リトライ前に待機
                except (requests.RequestException, requests.Timeout) as ex:
                    retries += 1
                    if retries >= max_retries:
                        raise ex
                    time.sleep(1)
            
            if resp.status_code != 200:
                logger.warning(f"JR西日本 {lname} APIステータスコード: {resp.status_code}")
                continue
            
            data = resp.json()
            has_data = True
            status = data.get("status", {}).get("text", "")
            detail = data.get("status", {}).get("detail", "")
            
            if should_include(status, detail):
                items.append({"路線名": lname, "運行状況": status, "詳細": detail or "詳細なし"})
                
        except Exception as ex:
            logger.warning(f"JR西日本 {lname} エラー: {ex}")
            items.append({"路線名": lname, "運行状況": "エラー", "詳細": f"{type(ex).__name__}: {str(ex)}"})
    
    if not items and has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "平常運転", "詳細": ""}]
    if not has_data:
        return [{"路線名": f"{area['name']}全線", "運行状況": "情報取得不可", "詳細": "取得失敗"}]
    
    return items

# --- Embed作成 ---
def create_east_embed(region: str, data: list[dict]) -> discord.Embed:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(title=f"🚆 JR東日本（{region}）運行情報", description=f"最終更新: {now}", color=0x2E8B57)
    
    # データがない場合の処理を追加
    if not data:
        emb.add_field(name="情報なし", value="現在、運行情報は取得できませんでした。", inline=False)
        return emb
        
    for x in data:
        # フィールド名と値の長さ制限を考慮
        name = f"{x['路線名']}：{x['運行状況']}"
        if len(name) > 256:
            name = name[:253] + "..."
            
        value = x['詳細'] if x['詳細'] else "詳細情報なし"
        if len(value) > 1024:
            value = value[:1021] + "..."
            
        emb.add_field(name=name, value=value, inline=False)
    
    return emb


def create_west_embed(area_code: str, data: list[dict]) -> discord.Embed:
    name = JR_WEST_LINES.get(area_code, {}).get("name", area_code)
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    emb = discord.Embed(title=f"🚆 JR西日本（{name}）運行情報", description=f"最終更新: {now}", color=0x4682B4)
    
    # データがない場合の処理を追加
    if not data:
        emb.add_field(name="情報なし", value="現在、運行情報は取得できませんでした。", inline=False)
        return emb
        
    for x in data:
        # フィールド名と値の長さ制限を考慮
        name = f"{x['路線名']}：{x['運行状況']}"
        if len(name) > 256:
            name = name[:253] + "..."
            
        value = x['詳細'] if x['詳細'] else "詳細情報なし"
        if len(value) > 1024:
            value = value[:1021] + "..."
            
        emb.add_field(name=name, value=value, inline=False)
    
    return emb

# ===== エラーハンドリング機能追加 =====
async def send_error_report(ch, message, error):
    """エラーレポートを送信する"""
    try:
        error_embed = discord.Embed(
            title="🔴 エラー発生",
            description=f"実行中にエラーが発生しました。\n```\n{message}\n```",
            color=0xFF0000
        )
        error_embed.add_field(name="エラー詳細", value=f"```\n{str(error)[:1000]}\n```", inline=False)
        error_embed.add_field(name="エラータイプ", value=f"`{type(error).__name__}`", inline=False)
        error_embed.add_field(
            name="対処方法", 
            value="このエラーが繰り返し発生する場合は、管理者に連絡してください。", 
            inline=False
        )
        await ch.send(embed=error_embed)
    except Exception as ex:
        logger.error(f"エラーレポート送信失敗: {ex}")

# ===== 定期更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    global REQUEST_CHANNEL
    if not REQUEST_CHANNEL:
        return
    
    ch = REQUEST_CHANNEL
    logger.info(f"自動更新投稿: チャンネル {ch.id}")
    
    try:
        # JR東日本情報取得
        for reg, code in YAHOO_AREAS.items():
            try:
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
            except Exception as ex:
                logger.exception(f"JR東日本 {reg} 自動更新エラー: {ex}")
                await send_error_report(ch, f"JR東日本 {reg} 自動更新中にエラーが発生しました。", ex)
        
        # JR西日本情報取得
        for area in JR_WEST_LINES.keys():
            try:
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
            except Exception as ex:
                logger.exception(f"JR西日本 {area} 自動更新エラー: {ex}")
                await send_error_report(ch, f"JR西日本 {area} 自動更新中にエラーが発生しました。", ex)
    
    except Exception as ex:
        logger.exception(f"自動更新全体エラー: {ex}")
        try:
            await send_error_report(ch, "自動更新処理中に重大なエラーが発生しました。", ex)
        except:
            pass

# ===== コマンド =====
@bot.command(name="運行情報")
async def train_info(ctx: commands.Context):
    global REQUEST_CHANNEL
    REQUEST_CHANNEL = ctx.channel
    
    status_msg = await ctx.send("🚅 運行情報を取得中です...")
    
    try:
        # JR東日本情報取得
        for reg, code in YAHOO_AREAS.items():
            try:
                await status_msg.edit(content=f"🚅 JR東日本（{reg}）の運行情報を取得中...")
                data = get_jr_east_filtered(reg, code)
                emb = create_east_embed(reg, data)
                msg = await ctx.send(embed=emb)
                train_messages['east'][reg] = msg
            except Exception as ex:
                logger.exception(f"JR東日本 {reg} 情報取得エラー: {ex}")
                await ctx.send(f"⚠️ JR東日本（{reg}）の情報取得中にエラーが発生しました。")
        
        # JR西日本情報取得
        for area in JR_WEST_LINES.keys():
            try:
                await status_msg.edit(content=f"🚅 JR西日本（{JR_WEST_LINES[area]['name']}）の運行情報を取得中...")
                data = get_jr_west_filtered(area)
                emb = create_west_embed(area, data)
                msg = await ctx.send(embed=emb)
                train_messages['west'][area] = msg
            except Exception as ex:
                logger.exception(f"JR西日本 {area} 情報取得エラー: {ex}")
                await ctx.send(f"⚠️ JR西日本（{JR_WEST_LINES[area]['name']}）の情報取得中にエラーが発生しました。")
        
        await status_msg.edit(content="✅ 運行情報の取得が完了しました！")
    
    except Exception as ex:
        logger.exception(f"運行情報コマンド全体エラー: {ex}")
        await status_msg.edit(content="❌ 運行情報の取得中に重大なエラーが発生しました。")
        await send_error_report(ctx.channel, "運行情報コマンド実行中にエラーが発生しました。", ex)

@bot.command(name="運行情報更新")
async def update_info(ctx: commands.Context):
    status_msg = await ctx.send("🔄 手動更新中...")
    
    try:
        await update_train_info()
        await status_msg.edit(content="✅ 更新完了しました！")
    except Exception as ex:
        logger.exception(f"手動更新エラー: {ex}")
        await status_msg.edit(content="❌ 更新中にエラーが発生しました。")
        await send_error_report(ctx.channel, "手動更新中にエラーが発生しました。", ex)

# ===== エラーハンドリング =====
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
        
    logger.exception(f"コマンドエラー: {error}")
    await send_error_report(ctx.channel, "コマンド実行中にエラーが発生しました。", error)

# ===== 起動時イベント =====
@bot.event
async def on_ready():
    logger.info(f"Bot 起動: {bot.user}")
    if not update_train_info.is_running():
        update_train_info.start()

# ===== エントリポイント =====
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.critical(f"Bot起動エラー: {e}")