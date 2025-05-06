import discord
from discord.ext import commands, tasks
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

# 環境変数から Discord トークン読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in environment variables")

# Discord Bot 設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# JR東日本 各地域 URL
JR_EAST_REGIONS = {
    "関東": "https://traininfo.jreast.co.jp/train_info/kanto.aspx",
    "東北": "https://traininfo.jreast.co.jp/train_info/tohoku.aspx",
    "信越": "https://traininfo.jreast.co.jp/train_info/shinetsu.aspx",
}

# JR西日本 API エリアコード→日本語名
JR_WEST_AREAS = {
    "hokuriku": "北陸",
    "kinki": "近畿",
    "chugoku": "中国",
    "shikoku": "四国",
    "kyushu": "九州",
}

# 自動更新用メッセージ保持
global msg_east, msg_west
msg_east = {}
msg_west = None

HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- JR東日本: requests + BeautifulSoup ---
def get_jr_east_info(region: str, url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        info = []
        for div in soup.select('.lineDetail'):
            name = div.select_one('.lineName').get_text(strip=True)
            status = div.select_one('.lineStatus').get_text(strip=True)
            detail_tag = div.select_one('.trouble span')
            detail = detail_tag.get_text(strip=True) if detail_tag else '詳細なし'
            info.append({'路線名': f'[{region}] {name}', '運行状況': status, '詳細': detail})
        if not info:
            info = [{'路線名': f'[{region}]', '運行状況': 'なし', '詳細': '情報が見つかりませんでした。'}]
        return info
    except Exception as e:
        logger.exception(f"JR東日本 {region} 取得失敗")
        return [{'路線名': f'[{region}] 取得失敗', '運行状況': 'エラー', '詳細': str(e)}]

# --- JR西日本: WestJR API ---
def get_jr_west_info():
    info = []
    for code, area in JR_WEST_AREAS.items():
        try:
            jr = WestJR(area=code)
            traffic: TrainInfo = jr.get_traffic_info()
            # 在来線
            for route_code, li in traffic.lines.items():
                route = jr.lines.get(route_code, route_code)
                info.append({'路線名': f'[西日本 {area}] {route}', '運行状況': li.status, '詳細': li.cause or '詳細なし'})
            # 特急
            for _, ei in traffic.express.items():
                info.append({'路線名': f'[西日本 {area} 特急] {ei.name}', '運行状況': ei.status, '詳細': ei.cause or '詳細なし'})
        except requests.exceptions.HTTPError as he:
            logger.warning(f'西日本 {area} スキップ: {he}')
        except Exception as e:
            logger.exception(f"JR西日本 {area} 取得失敗")
            info.append({'路線名': f'[西日本 {area}] 取得失敗', '運行状況': 'エラー', '詳細': str(e)})
    if not info:
        info = [{'路線名': '[西日本]', '運行状況': 'なし', '詳細': '情報が見つかりませんでした。'}]
    return info

# --- /運行情報 コマンド ---
@bot.command(name='運行情報')
async def train_info(ctx):
    # 東日本
    for region, url in JR_EAST_REGIONS.items():
        info = get_jr_east_info(region, url)
        embed = discord.Embed(title=f'🚆 JR東日本（{region}）運行情報', color=0x2e8b57)
        for item in info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text='30分ごとに自動更新されます')
        msg = await ctx.send(embed=embed)
        msg_east[region] = msg
    # 西日本
    west_info = get_jr_west_info()
    embed = discord.Embed(title='🚆 JR西日本運行情報', color=0x4682b4)
    for item in west_info:
        embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
    embed.set_footer(text='30分ごとに自動更新されます')
    global msg_west
    msg_west = await ctx.send(embed=embed)
    # 定期更新タスク開始
    if not update_loop.is_running():
        update_loop.start()

# --- 定期更新 ---
@tasks.loop(minutes=30)
async def update_loop():
    # 東日本
    for region, url in JR_EAST_REGIONS.items():
        info = get_jr_east_info(region, url)
        embed = discord.Embed(title=f'🚆 JR東日本（{region}）運行情報', color=0x2e8b57)
        for item in info:
            embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
        embed.set_footer(text='30分ごとに自動更新されます')
        await msg_east[region].edit(embed=embed)
    # 西日本
    west_info = get_jr_west_info()
    embed = discord.Embed(title='🚆 JR西日本運行情報', color=0x4682b4)
    for item in west_info:
        embed.add_field(name=f"{item['路線名']}：{item['運行状況']}", value=item['詳細'], inline=False)
    embed.set_footer(text='30分ごとに自動更新されます')
    await msg_west.edit(embed=embed)

# Bot起動
bot.run(TOKEN)