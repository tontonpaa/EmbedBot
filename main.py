import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import logging
from westjr import WestJR
from westjr.response_types import TrainInfo

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 環境変数からトークン読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in environment variables")

# Discord Bot 設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# HTTP ヘッダー
HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- JR東日本 各地域 URL ---
# 新潟・長野・甲信越を信越(甲信越)として koshinetsu.aspx に統合
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "信越": "https://traininfo.jreast.co.jp/train_info/shinetsu.aspx",
}

# --- JR西日本 API エリア マッピング ---
JR_WEST_AREAS = {
    "hokuriku": "北陸",
    "kinki": "近畿",
    # chugokuエリアは trafficinfo API未対応（404）ため除外
}

# 自動更新用メッセージ保持
global message_to_update_east, message_to_update_west
message_to_update_east = {}
message_to_update_west = None

# --- JR東日本: requests + BeautifulSoup スクレイピング ---
def get_jr_east_region_info(region: str, url: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        lines = soup.select('.lineDetail')
        info = []
        for line in lines:
            name = line.select_one('.lineName').get_text(strip=True)
            status = line.select_one('.lineStatus').get_text(strip=True)
            detail_tag = line.select_one('.trouble span')
            detail = detail_tag.get_text(strip=True) if detail_tag else '詳細なし'
            info.append({'路線名': f'[{region}] {name}', '運行状況': status, '詳細': detail})
        if not info:
            info.append({'路線名': f'[{region}]', '運行状況': 'なし', '詳細': '情報が見つかりませんでした。'})
        return info
    except Exception as e:
        logger.exception(f'JR東日本 {region} 取得エラー')
        return [{'路線名': f'[{region}] 取得失敗', '運行状況': 'エラー', '詳細': str(e)}]

# --- JR西日本: WestJR API ---
def get_jr_west_info() -> list[dict]:
    info = []
    for area_code, area_name in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=area_code)
            traffic: TrainInfo = jr.get_traffic_info()
            # 在来線情報
            for code, li in traffic.lines.items():
                route = jr.lines.get(code, code)
                info.append({'路線名': f'[西日本 {area_name}] {route}', '運行状況': li.status, '詳細': li.cause or '詳細なし'})
            # 特急情報
            for key, ei in traffic.express.items():
                info.append({'路線名': f'[西日本 特急] {ei.name}', '運行状況': ei.status, '詳細': ei.cause or '詳細なし'})
        except requests.exceptions.HTTPError as he:
            # 404等はエリア未対応としてスキップ
            logger.warning(f'JR西日本 {area_name} エリア情報取得スキップ: {he}')
        except Exception as e:
            logger.exception(f'JR西日本 {area_name} 取得エラー')
            info.append({'路線名': f'[西日本 {area_name}] 取得失敗', '運行状況': 'エラー', '詳細': str(e)})
    if not info:
        info.append({'路線名': '[西日本]', '運行状況': 'なし', '詳細': '情報が見つかりませんでした。'})
    return info

# --- /運行情報 コマンド ---
@tree.command(name='運行情報', description='JR東日本・西日本の運行情報を表示')
async def train_info_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        global message_to_update_east, message_to_update_west
        # 東日本
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f'🚆 JR東日本（{region}）運行情報', color=0x2e8b57)
            for item in info:
                embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed.set_footer(text='30分ごとに自動更新されます')
            msg = await interaction.followup.send(embed=embed)
            message_to_update_east[region] = msg
        # 西日本
        west_info = get_jr_west_info()
        embed = discord.Embed(title='🚆 JR西日本運行情報', color=0x4682b4)
        for item in west_info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text='30分ごとに自動更新されます')
        message_to_update_west = await interaction.followup.send(embed=embed)
        # 定期更新タスク起動
        if not update_embed.is_running():
            update_embed.start()
    except Exception:
        logger.exception('コマンド実行中にエラー')
        await interaction.followup.send('運行情報取得中にエラーが発生しました。')

# --- 定期更新タスク ---
@tasks.loop(minutes=30)
async def update_embed():
    global message_to_update_east, message_to_update_west
    try:
        for region, url in JR_EAST_REGIONS.items():
            info = get_jr_east_region_info(region, url)
            embed = discord.Embed(title=f'🚆 JR東日本（{region}）運行情報', color=0x2e8b57)
            for item in info:
                embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
            embed.set_footer(text='30分ごとに自動更新されます')
            if region in message_to_update_east:
                await message_to_update_east[region].edit(embed=embed)
        west_info = get_jr_west_info()
        embed = discord.Embed(title='🚆 JR西日本運行情報', color=0x4682b4)
        for item in west_info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text='30分ごとに自動更新されます')
        if message_to_update_west:
            await message_to_update_west.edit(embed=embed)
    except Exception:
        logger.exception('定期更新中にエラー')

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logger.info(f'Bot 起動成功: {bot.user}')
    except Exception:
        logger.error('スラッシュコマンド同期失敗')

if __name__ == '__main__':
    bot.run(TOKEN)