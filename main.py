import os
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
# STATE_FILE = "state.json" # JSONファイル関連部分を削除

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.presence_task_started = False
# target_channels: list[discord.TextChannel] # 型ヒントはPEP526形式でクラス/インスタンス変数として定義するのが一般的
target_channels: list[discord.TextChannel] = [] # ボットインスタンスの属性として初期化
bot.target_channels = []

YAHOO_EAST_AREAS = {"関東": 4, "東北": 3, "中部": 5}
YAHOO_WEST_AREAS = {"近畿": 6, "九州": 7, "中国": 8, "四国": 9}
DISRUPTION_KEYWORDS = ["運休", "運転見合わせ", "列車遅延", "その他", "運転計画", "運行情報"]

# ===== ブロック処理スレッド化 =====
def _fetch_area_info_sync(region: str, code: int) -> list[dict]:
    base = "https://transit.yahoo.co.jp"
    url = f"{base}/diainfo/area/{code}"
    headers = {"User-Agent": "Mozilla/5.0"} # 定義済みのヘッダーを使用
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[dict] = []

    for div in soup.select("div.elmTblLstLine"):
        tbl = div.find("table")
        if not tbl:
            continue
        # HTML構造によっては tbody が存在しない、または複数存在する場合があるので注意
        # select("tbody > tr") は tbody 直下の tr のみを選択
        # select("tr") でテーブル内の全trを選択し、ヘッダー行をスライスで除外する方が堅牢な場合もある
        for tr in tbl.select("tbody > tr")[1:]: # ヘッダー行を除外
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            status = cols[1].get_text(strip=True)
            detail_preview = cols[2].get_text(strip=True)
            # DISRUPTION_KEYWORDSに合致しない場合はスキップ
            if not (any(kw in status for kw in DISRUPTION_KEYWORDS)
                    or any(kw in detail_preview for kw in DISRUPTION_KEYWORDS)):
                continue

            a_tag = cols[0].find("a", href=True) # 変数名を a から a_tag に変更 (可読性のため)
            name = cols[0].get_text(strip=True)
            detail = detail_preview # 初期値を設定
            if a_tag:
                link = base + a_tag["href"]
                try:
                    lr = requests.get(link, headers=headers, timeout=15)
                    lr.raise_for_status()
                    lsoup = BeautifulSoup(lr.text, "html.parser")
                    # 詳細ページでの情報取得
                    h1 = lsoup.select_one("div.labelLarge h1.title") # Yahooのページ構成変更に注意
                    name = h1.get_text(strip=True) if h1 else name
                    dd = lsoup.select_one("dd.trouble p") # Yahooのページ構成変更に注意
                    detail = dd.get_text(strip=True) if dd else detail
                except requests.RequestException as e_req: # ネットワークエラー等
                    logger.warning(f"詳細情報取得失敗 ({link}): {e_req}")
                    # pass だとエラーが握りつぶされるので、少なくともログには残す
                except Exception as e_parse: # パースエラー等
                    logger.warning(f"詳細情報パース失敗 ({link}): {e_parse}")
                    # pass

            items.append({
                "路線名": name,
                "運行状況": status,
                "詳細": detail
            })

    if not items: # 運行障害がない場合
        return [{"路線名": f"{region}全線", "運行状況": "平常運転", "詳細": "現在、運行情報の発表はありません。"}] # 詳細を具体的に
    return items

async def fetch_area_info(region: str, code: int) -> list[dict]:
    return await asyncio.to_thread(_fetch_area_info_sync, region, code)

# ===== Embed 分割送信 =====
async def send_paginated_embeds(prefix: str, region: str, data: list[dict], color: int, channel: discord.TextChannel):
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M")
    per_page = 25 # 1つのEmbedに含めるフィールド数の上限
    pages = (len(data) + per_page - 1) // per_page # 必要なページ数

    if not data: # データがない場合（通常は起こらないはずだが念のため）
        logger.warning(f"{prefix} ({region}) の送信データが空です。")
        emb = discord.Embed(
            title=f"🚆 {prefix}（{region}） 運行情報",
            description=f"情報がありませんでした。\n最終更新: {now}",
            color=color
        )
        await channel.send(embed=emb)
        return

    for i in range(pages):
        emb = discord.Embed(
            title=f"🚆 {prefix}（{region}） 運行情報 ({i+1}/{pages})",
            description=f"最終更新: {now}",
            color=color
        )
        start_index = i * per_page
        end_index = start_index + per_page
        for entry in data[start_index:end_index]:
            # フィールド名の長さ制限に注意 (256文字)
            name_field = f"{entry['路線名']}：{entry['運行状況']}"
            if len(name_field) > 256:
                name_field = name_field[:253] + "..." # 制限超過の場合は省略
            # フィールド値の長さ制限に注意 (1024文字)
            value_field = entry['詳細'] or "詳細情報なし"
            if len(value_field) > 1024:
                value_field = value_field[:1021] + "..." # 制限超過の場合は省略
            emb.add_field(name=name_field, value=value_field, inline=False)
        
        if not emb.fields: # 何らかの理由でフィールドが空になった場合 (通常はないはず)
            logger.warning(f"{prefix} ({region}) のEmbedからフィールドが消失しました (ページ {i+1})。")
            # このページには情報がない旨を伝えるか、送信をスキップするか
            emb.description = f"{emb.description}\n\nこのページには表示する情報がありません。"
        
        await channel.send(embed=emb)

# ===== エラー通知 =====
async def send_error_report(ch: discord.TextChannel, message: str, error: Exception):
    emb = discord.Embed(title="🔴 エラー発生", description=message, color=0xFF0000)
    # エラーメッセージが長すぎる場合も考慮
    error_details = str(error)
    if len(error_details) > 1000: # Discordのフィールド値上限より少し短く
        error_details = error_details[:1000] + "..."
    tb_str = traceback.format_exc()
    if len(tb_str) > 1000:
        tb_str = tb_str[:1000] + "..."
    emb.add_field(name="エラー概要", value=f"```\n{error_details}\n```", inline=False)
    emb.add_field(name="トレースバック抜粋", value=f"```\n{tb_str}\n```", inline=False) # トレースバックも追加するとデバッグに役立つ
    await ch.send(embed=emb)

# ===== 自動更新タスク =====
@tasks.loop(minutes=30)
async def update_train_info():
    logger.info("自動更新タスク開始")
    if not bot.target_channels:
        logger.info("通知対象チャンネルがないため、自動更新をスキップします。")
        return

    for ch in bot.target_channels:
        logger.info(f"チャンネル {ch.name} ({ch.id}) に運行情報を送信します。")
        # 東日本
        for region, code in YAHOO_EAST_AREAS.items():
            try:
                logger.info(f"JR東日本 {region} の情報を取得中...")
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR東日本", region, data, 0x2E8B57, ch)
                logger.info(f"JR東日本 {region} の情報を送信完了。")
            except Exception as e:
                logger.error(f"JR東日本 {region} 更新失敗: {e}", exc_info=True)
                await send_error_report(ch, f"JR東日本 {region} の運行情報更新に失敗しました。", e)
        # 西日本
        for region, code in YAHOO_WEST_AREAS.items():
            try:
                logger.info(f"JR西日本 {region} の情報を取得中...")
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR西日本", region, data, 0x4682B4, ch)
                logger.info(f"JR西日本 {region} の情報を送信完了。")
            except Exception as e:
                logger.error(f"JR西日本 {region} 更新失敗: {e}", exc_info=True)
                await send_error_report(ch, f"JR西日本 {region} の運行情報更新に失敗しました。", e)
    logger.info("自動更新タスク完了")

@update_train_info.before_loop
async def before_update_train_info():
    await bot.wait_until_ready() # ボットが完全に準備できるまで待つ
    logger.info("自動更新タスクの準備完了。ループを開始します。")

@update_train_info.error
async def update_error(error):
    logger.error(f"自動更新タスクで予期せぬエラーが発生しました: {error}")
    traceback.print_exc()
    # ここで特定のチャンネルにエラーを通知することも検討できる
    # 例えば、オーナーへのDMや特定のログチャンネルなど
    # for ch in bot.target_channels: # もし運用チャンネルに通知する場合
    #     await send_error_report(ch, "自動更新タスク全体でエラーが発生しました。", error)

# ===== イベント =====
@bot.event
async def on_ready():
    logger.info(f"Bot 起動完了: {bot.user} (ID: {bot.user.id})")
    # await bot.wait_until_ready() # ここでは不要、タスクの before_loop やコマンド内で待つ

    # 「運行情報」を含むチャンネルを全ギルドから収集
    bot.target_channels.clear()
    logger.info("通知対象チャンネルを収集します...")
    for guild in bot.guilds:
        logger.debug(f"ギルド: {guild.name} ({guild.id}) を確認中")
        for channel in guild.text_channels:
            # チャンネル名に「運行情報」が含まれ、かつボットがメッセージ送信権限を持つチャンネル
            if "運行情報" in channel.name and channel.permissions_for(guild.me).send_messages:
                logger.info(f"対象チャンネル発見: {channel.name} ({channel.id}) in {guild.name}")
                bot.target_channels.append(channel)
            # else: # デバッグ用
            #     if "運行情報" in channel.name:
            #         logger.debug(f"チャンネル '{channel.name}' は名前に '運行情報' を含みますが、送信権限がありません。")

    if not bot.target_channels:
        logger.warning("運行情報を通知する対象チャンネルが見つかりませんでした。")
    else:
        logger.info(f"合計 {len(bot.target_channels)} 個のチャンネルに運行情報を通知します。")
        # 起動時に一度送信 & 30分ごとの自動更新開始
        # update_train_info.start() が初回実行も行うため、ここでの明示的な呼び出しは不要
        if not update_train_info.is_running():
            logger.info("自動更新タスクを開始します。初回実行はタスク開始時に行われます。")
            update_train_info.start()
        else:
            logger.info("自動更新タスクは既に実行中です。")

    if not bot.presence_task_started:
        logger.info("プレゼンス更新タスクを開始します。")
        bot.loop.create_task(update_presence())
        bot.presence_task_started = True
    else:
        logger.info("プレゼンス更新タスクは既に開始されています。")

async def update_presence():
    await bot.wait_until_ready()
    logger.info("プレゼンス更新ループ開始")
    while True:
        try:
            ping = round(bot.latency * 1000)
            await bot.change_presence(activity=discord.Game(name=f"Ping: {ping}ms"))
            await asyncio.sleep(10) # 表示時間を調整 (5秒から10秒へ)
            
            server_count = len(bot.guilds)
            await bot.change_presence(activity=discord.Game(name=f"サーバー数: {server_count}"))
            await asyncio.sleep(10) # 表示時間を調整
            
            # 運行情報チャンネル数を表示する例
            # target_channel_count = len(bot.target_channels)
            # await bot.change_presence(activity=discord.Game(name=f"通知ch数: {target_channel_count}"))
            # await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"[update_presence エラー] {e}", exc_info=True)
            await asyncio.sleep(20) # エラー発生時は少し長めに待機

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        # logger.debug(f"存在しないコマンドが実行されました: {ctx.message.content}") # 必要であればログに記録
        return # 不明なコマンドの場合は何もしない
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(f"{ctx.author.mention} コマンドの実行に必要な権限がありません。", ephemeral=True) # ephemeralはスラッシュコマンド用、テキストコマンドではエラー
        logger.warning(f"権限不足: {ctx.author} が {ctx.command} を実行しようとしましたが権限がありません: {error}")
    elif isinstance(error, commands.CommandInvokeError):
        logger.error(f"コマンド '{ctx.command}' の実行中にエラーが発生しました: {error.original}", exc_info=error.original)
        await send_error_report(ctx.channel, f"コマンド「{ctx.command.name}」の実行中にエラーが発生しました。", error.original)
    else:
        logger.error(f"予期せぬコマンドエラー: {error} (コマンド: {ctx.command})", exc_info=True)
        await send_error_report(ctx.channel, "コマンド実行中に予期せぬエラーが発生しました。", error)

# ===== 手動コマンド =====
@bot.command(name="運行情報")
@commands.cooldown(1, 60, commands.BucketType.user) # ユーザーごとに1分間に1回まで実行可能にする例
async def manual_info(ctx: commands.Context):
    """手動で全対象チャンネルに運行情報を送信します。"""
    await ctx.message.add_reaction("🔄") # 処理中であることを示すリアクション
    logger.info(f"手動コマンド '!運行情報' が {ctx.author} により実行されました。")

    if not bot.target_channels:
        await ctx.send("運行情報を通知する対象チャンネルが設定されていません。", delete_after=30)
        logger.warning("手動コマンド実行：通知対象チャンネルなし。")
        await ctx.message.remove_reaction("🔄", bot.user)
        await ctx.message.add_reaction("⚠️")
        return

    # 手動コマンドの場合は、コマンドが実行されたチャンネルにも通知するか、
    # もしくは、既存の target_channels のみに通知するかを選択できます。
    # 現在の実装は全 target_channels に送信します。
    # ここでは、コマンド実行者に応答を返すことを優先し、処理結果を伝えるようにします。
    sent_count = 0
    error_count = 0

    for channel in bot.target_channels:
        logger.info(f"手動更新: チャンネル {channel.name} ({channel.id}) に送信開始")
        # 東日本
        for region, code in YAHOO_EAST_AREAS.items():
            try:
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR東日本 (手動)", region, data, 0x2E8B57, channel)
                sent_count +=1
            except Exception as e:
                error_count +=1
                logger.error(f"手動 JR東日本 {region} ({channel.name}) 取得送信失敗: {e}", exc_info=True)
                await send_error_report(channel, f"手動更新 JR東日本 {region} の情報取得・送信に失敗しました。", e)
        # 西日本
        for region, code in YAHOO_WEST_AREAS.items():
            try:
                data = await fetch_area_info(region, code)
                await send_paginated_embeds("JR西日本 (手動)", region, data, 0x4682B4, channel)
                sent_count +=1
            except Exception as e:
                error_count +=1
                logger.error(f"手動 JR西日本 {region} ({channel.name}) 取得送信失敗: {e}", exc_info=True)
                await send_error_report(channel, f"手動更新 JR西日本 {region} の情報取得・送信に失敗しました。", e)
        logger.info(f"手動更新: チャンネル {channel.name} ({channel.id}) への送信完了")
    
    await ctx.message.remove_reaction("🔄", bot.user)
    if error_count == 0 and sent_count > 0:
        await ctx.send(f"{ctx.author.mention} 全対象チャンネル（{len(bot.target_channels)}件）に運行情報を更新・送信しました。", delete_after=60)
        await ctx.message.add_reaction("✅")
    elif sent_count > 0 and error_count > 0:
        await ctx.send(f"{ctx.author.mention} 運行情報の一部の送信に失敗しました。詳細は各チャンネルやログを確認してください。", delete_after=60)
        await ctx.message.add_reaction("⚠️")
    elif error_count > 0 and sent_count == 0:
        await ctx.send(f"{ctx.author.mention} 全ての運行情報の送信に失敗しました。詳細は各チャンネルやログを確認してください。", delete_after=60)
        await ctx.message.add_reaction("❌")
    else: # sent_count == 0 and error_count == 0 (通常 target_channels があればここには来ない)
         await ctx.send(f"{ctx.author.mention} 実行されましたが、送信処理が行われませんでした。設定を確認してください。", delete_after=60)
         await ctx.message.add_reaction("❓")


if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN が設定されていません。.envファイルを確認してください。")
    else:
        try:
            logger.info("Botを起動します...")
            bot.run(TOKEN)
        except discord.LoginFailure:
            logger.critical("Discordへのログインに失敗しました。トークンが正しいか確認してください。")
        except Exception as e:
            logger.critical(f"Botの起動中に予期せぬエラーが発生しました: {e}", exc_info=True)