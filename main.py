import os
import http.server
import threading
import discord
import asyncio
import random
import json
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone

# ========================================================
# 【Render強制終了対策】正しいポートで即座に応答するダミーサーバー
# ========================================================
class MyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot is running!".encode("utf-8"))

    def log_message(self, format, *args):
        return

def run_dummy_server():
    port = int(os.environ.get('PORT', 8080))
    server = http.server.HTTPServer(('0.0.0.0', port), MyHandler)
    print(f"【システム】Render用ダミーサーバーをポート {port} で起動しました。")
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# ========================================================

# 通知先 兼 データバックアップチャンネルのID
LOG_CHANNEL_ID = 1518765558160691230

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
intents.reactions = True

DATA_FILE = "bot_data.json"

warn_data = {}
active_polls = {}
xp_data = {}        
xp_enabled = True   
last_xp_time = {}   

# /auto用のアナウンス設定
auto_announce_channel_id = None
auto_msg_count = 1

def save_all_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "warn_data": warn_data,
                "xp_data": xp_data,
                "xp_enabled": xp_enabled,
                "auto_announce_channel_id": auto_announce_channel_id
            }, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"データファイルのローカル保存に失敗しました: {e}")

async def send_channel_log(bot_instance, embed):
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if channel:
            await channel.send(embed=embed)
    except Exception as e:
        print(f"チャンネルログの送信に失敗しました: {e}")

# ========================================================
# 【NEW】Discordのログチャンネルを使ったクラウドバックアップ・復元システム
# ========================================================
async def backup_data_to_discord(bot_instance):
    """データをJSON文字列にしてログチャンネルに投稿する（消滅対策）"""
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if channel:
            data_payload = {
                "warn_data": warn_data,
                "xp_data": xp_data,
                "xp_enabled": xp_enabled,
                "auto_announce_channel_id": auto_announce_channel_id
            }
            json_str = json.dumps(data_payload, ensure_ascii=False)
            # Discordの文字数制限（2000文字）を考慮し、コードブロック形式で送信
            # データが非常に大きくなる場合はファイルとして送信
            if len(json_str) < 1900:
                await channel.send(f"||BOT_BACKUP_DATA_START||\n```json\n{json_str}\n
```\n||BOT_BACKUP_DATA_END||")
            else:
                with open("backup_temp.json", "w", encoding="utf-8") as tmp:
                    json.dump(data_payload, tmp, ensure_ascii=False)
                await channel.send("【データバックアップファイル】", file=discord.File("backup_temp.json", filename="bot_backup.json"))
    except Exception as e:
        print(f"Discordへの自動バックアップ送信に失敗しました: {e}")

async def load_data_from_discord(bot_instance):
    """ログチャンネルの過去ログから最新のバックアップデータを検索して復元する"""
    global warn_data, xp_data, xp_enabled, auto_announce_channel_id
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if not channel:
            return
        
        print("【システム】Discordのログチャンネルから過去のバックアップデータを捜索中...")
        async for message in channel.history(limit=100):
            # 1. テキストメッセージ形式のバックアップをパース
            if "||BOT_BACKUP_DATA_START||" in message.content and message.author.id == bot_instance.user.id:
                try:
                    content = message.content.split("```json\n")[1].split("\n```")[0]
                    loaded = json.loads(content)
                    warn_data = loaded.get("warn_data", {})
                    xp_data = loaded.get("xp_data", {})
                    xp_enabled = loaded.get("xp_enabled", True)
                    auto_announce_channel_id = loaded.get("auto_announce_channel_id", None)
                    print("✅ Discordのテキスト履歴からデータを正常に復元しました！")
                    save_all_data()
                    return
                except Exception:
                    continue
            
            # 2. ファイル形式のバックアップをパース
            if message.attachments and message.author.id == bot_instance.user.id:
                for attachment in message.attachments:
                    if attachment.filename == "bot_backup.json":
                        try:
                            file_bytes = await attachment.read()
                            loaded = json.loads(file_bytes.decode("utf-8"))
                            warn_data = loaded.get("warn_data", {})
                            xp_data = loaded.get("xp_data", {})
                            xp_enabled = loaded.get("xp_enabled", True)
                            auto_announce_channel_id = loaded.get("auto_announce_channel_id", None)
                            print("✅ Discordの添付ファイルからデータを正常に復元しました！")
                            save_all_data()
                            return
                        except Exception:
                            continue
        print("ℹ️ 有効なバックアップデータがDiscord上に見つかりませんでした。新規作成します。")
    except Exception as e:
        print(f"Discordからのデータ復元中にエラーが発生しました: {e}")

# ========================================================
# 定期ループ処理（警告期限チェック＆3分間隔アナウンス）
# ========================================================
@tasks.loop(minutes=1)
async def check_warn_expiry():
    try:
        now = datetime.now(timezone.utc)
        expired_users = []
        for user_id, data in warn_data.items():
            if data.get("count", 0) > 0 and now >= datetime.fromisoformat(data["expire_at"]):
                expired_users.append(user_id)
        for user_id in expired_users:
            warn_data[user_id]["count"] = 0
            warn_data[user_id]["logs"].append({
                "count": 0,
                "reason": "【システム自動解除】3週間が経過したため警告がすべてリセットされました。",
                "date": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            })
            save_all_data()
            await backup_data_to_discord(bot)
            embed = discord.Embed(title="⏰ 警告の自動システム解除通知", color=discord.Color.blue())
            embed.add_field(name="対象ユーザーID", value=f"<@{user_id}> (`{user_id}`)", inline=False)
            embed.add_field(name="内容", value="3週間経過したため、警告カウントが自動リセットされました。", inline=False)
            await send_channel_log(bot, embed)
    except Exception as e:
        print(f"警告解除処理でエラーが発生しました: {e}")

@tasks.loop(minutes=3)
async def auto_announce_loop():
    """【NEW】/auto で設定されたチャンネルに3分ごとに生存メッセージを送るループ"""
    global auto_announce_channel_id, auto_msg_count
    if auto_announce_channel_id is None:
        return
    try:
        channel = bot.get_channel(auto_announce_channel_id) or await bot.fetch_channel(auto_announce_channel_id)
        if channel:
            now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
            await channel.send(f"🤖 **【定期生存確認】** ボットは現在も正常に動作中だよ！ (確認回数: {auto_msg_count}回目 / 時刻: {now_str})")
            auto_msg_count += 1
    except Exception as e:
        print(f"定期アナウンスの送信に失敗しました: {e}")

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, heartbeat_timeout=120.0)

    async def setup_hook(self):
        check_warn_expiry.start()
        auto_announce_loop.start()
        asyncio.create_task(self.delayed_sync())

    async def delayed_sync(self):
        await asyncio.sleep(5)
        # 起動直後にDiscordからデータをダウンロードして復元
        await load_data_from_discord(self)
        try:
            await self.tree.sync()
            print("スラッシュコマンドを同期しました。")
        except Exception as e:
            print(f"コマンドの同期に失敗しました（動作は続行します）: {e}")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name}")

# ========================================================
# レベルシステム用ヘルパー関数
# ========================================================
def get_next_level_xp(level):
    return level * 100

def should_have_role(level):
    if level >= 500: return 500
    elif level >= 200: return (level // 50) * 50
    elif level >= 100: return (level // 10) * 10
    elif level >= 5: return (level // 5) * 5
    return None

async def check_and_update_level_roles(member, level):
    target_lvl = should_have_role(level)
    role_name_prefix = "Level "
    
    target_role = None
    if target_lvl is not None:
        target_role_name = f"{role_name_prefix}{target_lvl}"
        target_role = discord.utils.get(member.guild.roles, name=target_role_name)
        
        if not target_role:
            try:
                target_role = await member.guild.create_role(name=target_role_name, color=discord.Color.from_rgb(46, 204, 113), reason="レベルシステムによる自動作成")
            except discord.Forbidden:
                print(f"ロールの作成権限がありません: {target_role_name}")
                return

    try:
        if target_role and target_role not in member.roles:
            await member.add_roles(target_role)
        
        for r in member.roles:
            if r.name.startswith(role_name_prefix) and (target_role is None or r.id != target_role.id):
                await member.remove_roles(r)
    except discord.Forbidden:
        print(f"メンバー {member.name} へのロール操作権限が不足しています。")

async def add_xp(member, amount):
    global xp_enabled
    if not xp_enabled: return

    user_id = str(member.id)
    if user_id not in xp_data:
        xp_data[user_id] = {"level": 1, "xp": 0}

    current_level = xp_data[user_id]["level"]
    current_xp = xp_data[user_id]["xp"] + amount

    if current_level >= 500:
        xp_data[user_id]["xp"] = 0
        xp_data[user_id]["level"] = 500
        save_all_data()
        return

    leveled_up = False
    while current_xp >= get_next_level_xp(current_level):
        current_xp -= get_next_level_xp(current_level)
        current_level += 1
        leveled_up = True
        if current_level >= 500:
            current_level = 500
            current_xp = 0
            break

    xp_data[user_id]["level"] = current_level
    xp_data[user_id]["xp"] = current_xp
    save_all_data()

    if leveled_up:
        await check_and_update_level_roles(member, current_level)
        # レベルアップ時は重要なデータ変化なのでDiscordに即時バックアップ
        await backup_data_to_discord(bot)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return

    user_id = message.author.id
    now = datetime.now(timezone.utc)
    if user_id not in last_xp_time or now - last_xp_time[user_id] > timedelta(minutes=1):
        last_xp_time[user_id] = now
        xp_to_add = random.randint(10, 25)
        await add_xp(message.author, xp_to_add)

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    user_id = str(member.id)
    if user_id not in xp_data:
        xp_data[user_id] = {"level": 1, "xp": 0}
        save_all_data()
        await backup_data_to_discord(bot)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id or not payload.guild_id: return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return
    member = payload.member
    if not member or member.bot: return

    xp_to_add = random.randint(2, 5)
    await add_xp(member, xp_to_add)

# ========================================================
# スラッシュコマンド群
# ========================================================

@bot.tree.command(name="auto", description="3分ごとの定期生存メッセージを送信するチャンネルを設定・変更します")
@app_commands.describe(channel="定期メッセージを送信したいテキストチャンネルを選択してください。解除する場合は未選択か現在の設定と同じにしてください。")
async def auto(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """【NEW】/auto コマンドの実装"""
    global auto_announce_channel_id, auto_msg_count
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("このコマンドを実行する権限（サーバー管理権限）がありません。", ephemeral=True)
        return

    if channel is None:
        auto_announce_channel_id = None
        save_all_data()
        await backup_data_to_discord(bot)
        await interaction.response.send_message("❌ 定期生存メッセージの設定を解除しました。")
        return

    auto_announce_channel_id = channel.id
    auto_msg_count = 1  # カウントをリセット
    save_all_data()
    await backup_data_to_discord(bot)
    
    await interaction.response.send_message(f"✅ {channel.mention} を3分ごとの定期生存確認メッセージの送信先に設定しました！")
    
    # 即座に1回目のメッセージを送信テスト
    try:
        now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        await channel.send(f"🤖 **【定期生存確認】** /auto によりここがアナウンス先に設定されたよ！ (時刻: {now_str})")
        auto_msg_count += 1
    except Exception as e:
        print(f"初期メッセージの送信に失敗しました: {e}")

@bot.tree.command(name="rolecreate", description="レベルシステム用（5〜500レベ）の全ロールを自動で一括作成します")
async def rolecreate(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("このコマンドを実行する権限（ロール管理権限）がありません。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    levels_to_create = []
    for l in range(5, 100, 5): levels_to_create.append(l)
    for l in range(100, 200, 10): levels_to_create.append(l)
    for l in range(200, 501, 50): levels_to_create.append(l)

    created_count = 0
    skipped_count = 0

    try:
        for lvl in levels_to_create:
            role_name = f"Level {lvl}"
            existing_role = discord.utils.get(interaction.guild.roles, name=role_name)
            
            if not existing_role:
                await interaction.guild.create_role(name=role_name, color=discord.Color.from_rgb(46, 204, 113), reason="レベルシステム一括初期作成")
                created_count += 1
                await asyncio.sleep(0.5)
            else:
                skipped_count += 1

        status_msg = f"✅ ロールの作成が完了しました！\n・新しく作成したロール: `{created_count}` 個\n・既に存在したためスキップ: `{skipped_count}` 個"
        await interaction.followup.send(status_msg)

        embed = discord.Embed(title="🛠️ コマンドログ: /rolecreate 実行", color=discord.Color.blue())
        embed.add_field(name="サーバー名", value=interaction.guild.name, inline=True)
        embed.add_field(name="実行者", value=interaction.user.mention, inline=True)
        embed.add_field(name="結果", value=f"作成: {created_count}個 / スキップ: {skipped_count}個", inline=False)
        await send_channel_log(bot, embed)

    except discord.Forbidden:
        await interaction.followup.send("❌ ボットに「ロールの管理」権限が付与されていないか、権限が不足しているため作成に失敗しました。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {e}")

@bot.tree.command(name="xpmode", description="レベル・XP機能の有効/無効を切り替えます")
@app_commands.describe(mode="オンにする場合は on、オフにする場合は off を選択してください")
@app_commands.choices(mode=[
    app_commands.Choice(name="🟢 オン (ON)", value="on"),
    app_commands.Choice(name="🔴 オフ (OFF)", value="off")
])
async def xpmode(interaction: discord.Interaction, mode: str):
    global xp_enabled
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("このコマンドを実行する権限（サーバー管理権限）がありません。", ephemeral=True)
        return

    if mode == "on":
        xp_enabled = True
        status_text = "🟢 有効 (ON)"
    else:
        xp_enabled = False
        status_text = "🔴 無効 (OFF)"

    save_all_data()
    await backup_data_to_discord(bot)
    await interaction.response.send_message(f"⚙️ レベル・XPシステムを **{status_text}** に設定しました。")
    
    embed = discord.Embed(title="⚙️ コマンドログ: /xpmode 設定変更", color=discord.Color.orange())
    embed.add_field(name="サーバー名", value=interaction.guild.name, inline=True)
    embed.add_field(name="実行者", value=interaction.user.mention, inline=True)
    embed.add_field(name="変更後の状態", value=status_text, inline=False)
    await send_channel_log(bot, embed)

@bot.tree.command(name="level", description="自分または指定したメンバーのレベルとXP情報を確認します")
@app_commands.describe(member="レベルを確認したいメンバーを選択（省略すると自分）")
async def level(interaction: discord.Interaction, member: discord.Member = None):
    target_member = member or interaction.user
    user_id = str(target_member.id)

    if user_id not in xp_data:
        xp_data[user_id] = {"level": 1, "xp": 0}
        save_all_data()

    lvl = xp_data[user_id]["level"]
    xp = xp_data[user_id]["xp"]
    next_xp = get_next_level_xp(lvl)

    embed = discord.Embed(title=f"📊 {target_member.display_name} のレベルステータス", color=discord.Color.green())
    embed.set_thumbnail(url=target_member.display_avatar.url)
    embed.add_field(name="現在のレベル", value=f"🆙 **Lv {lvl}** / 500", inline=True)
    
    if lvl >= 500:
        embed.add_field(name="XP カウント", value="✨ **MAX LEVEL**", inline=True)
    else:
        embed.add_field(name="XP カウント", value=f"✨ `{xp}` / `{next_xp}` XP", inline=True)
        bar_length = 10
        progress = int((xp / next_xp) * bar_length)
        bar = "🟩" * progress + "⬜" * (bar_length - progress)
        embed.add_field(name="次のレベルまで", value=bar, inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="levelset", description="指定したメンバーのレベルを強制的に設定します")
@app_commands.describe(member="レベルを設定したいメンバーを選択してください", target_level="設定するレベル（1〜500）を入力してください")
async def levelset(interaction: discord.Interaction, member: discord.Member, target_level: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("このコマンドを実行する権限（サーバー管理権限）がありません。", ephemeral=True)
        return

    if target_level < 1 or target_level > 500:
        await interaction.response.send_message("レベルは 1 から 500 の間で指定してください。", ephemeral=True)
        return

    user_id = str(member.id)
    xp_data[user_id] = {"level": target_level, "xp": 0}
    save_all_data()
    await backup_data_to_discord(bot)

    await check_and_update_level_roles(member, target_level)
    await interaction.response.send_message(f"🔧 {member.mention} さんのレベルを **Level {target_level}** に変更しました。（XPは0にリセットされました）")

    embed = discord.Embed(title="🔧 コマンドログ: /levelset 実行", color=discord.Color.purple())
    embed.add_field(name="サーバー名", value=interaction.guild.name, inline=True)
    embed.add_field(name="実行者", value=interaction.user.mention, inline=True)
    embed.add_field(name="対象者", value=member.mention, inline=True)
    embed.add_field(name="変更後のレベル", value=f"Lv {target_level}", inline=False)
    await send_channel_log(bot, embed)

class PollSelect(discord.ui.Select):
    def __init__(self, message_id, options_list, max_values=1):
        super().__init__(
            placeholder="ここをタップして投票する選択肢を選んでください...",
            min_values=1, max_values=max_values,
            options=[discord.SelectOption(label=opt, value=opt) for opt in options_list],
            custom_id=f"poll_select_{message_id}"
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if self.message_id not in active_polls:
            active_polls[self.message_id] = {}
        active_polls[self.message_id][interaction.user.id] = self.values
        chosen_text = ", ".join([f"**{v}**" for v in self.values])
        await interaction.response.send_message(f"✅ {chosen_text} に投票しました！変更したい場合は再度選び直せます。", ephemeral=True)

class PollView(discord.ui.View):
    def __init__(self, message_id, options_list, max_values=1):
        super().__init__(timeout=None)
        self.add_item(PollSelect(message_id, options_list, max_values))

async def poll_timer(guild_id, channel_id, message_id, minutes, title, valid_choices):
    await asyncio.sleep(minutes * 60)
    try:
        guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        
        votes = active_polls.pop(message_id, {})
        choice_counts = {choice: [] for choice in valid_choices}
        unique_voters = set()
        total_votes = 0
        
        for user_id, chosen_list in votes.items():
            if chosen_list:
                unique_voters.add(user_id)
            for chosen in chosen_list:
                if chosen in choice_counts:
                    choice_counts[chosen].append(f"<@{user_id}>")
                    total_votes += 1
                    
        result_text = "ーー 【集計結果】 ーー\n"
        for choice in valid_choices:
            voters = choice_counts[choice]
            count = len(voters)
            if count > 0:
                result_text += f"🔹 **{choice}**: `{count}` 人\n└ 投票者: {', '.join(voters)}\n"
            else:
                result_text += f"🔹 **{choice}**: `0` 人\n└ 投票者: なし\n"
        
        result_text += f"\n📊 投票参加人数: `{len(unique_voters)}` 人 (総得票数: `{total_votes}` 票)"

        end_embed = message.embeds[0]
        end_embed.title = f"🔒 【終了】投票：{title}"
        end_embed.description = f"⏰ 投票時間は終了しました。\n\n{result_text}"
        end_embed.color = discord.Color.light_grey()
        await message.edit(content=None, embed=end_embed, view=None)
        
        embed_log = discord.Embed(title="🔒 コマンドログ: 投票が終了しました", color=discord.Color.light_grey())
        embed_log.add_field(name="サーバー名", value=f"**{guild.name}**", inline=True)
        embed_log.add_field(name="チャンネル", value=f"<#{channel_id}>", inline=True)
        embed_log.add_field(name="投票テーマ", value=title, inline=False)
        embed_log.add_field(name="最終結果と投票者内訳", value=result_text, inline=False)
        await send_channel_log(bot, embed_log)
    except Exception as e:
        print(f"投票の自動締め切り処理でエラーが発生しました: {e}")

@bot.tree.command(name="mo", description="指定したチャンネルにメニュー選択式の投票を作成します（最大10択）")
@app_commands.describe(
    channel="投票を出したいチャンネルを選択してください", title="投票の本文（タイトル）を入力してください",
    minutes="投票の制限時間を【分】で入力してください", mode="1つだけ選択させるか、複数選択を許可するかを選んでください",
    choice1="選択肢1（必須）", choice2="選択肢2（必須）", choice3="選択肢3（任意）", choice4="選択肢4（任意）", choice5="選択肢5（任意）",
    choice6="選択肢6（任意）", choice7="選択肢7（任意）", choice8="選択肢8（任意）", choice9="選択肢9（任意）", choice10="選択肢10（任意）"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="☝️ 1つだけ選択可能（単一選択）", value="single"),
    app_commands.Choice(name="🌟 複数選択可能（選べるだけすべて）", value="multiple")
])
async def mo(
    interaction: discord.Interaction, channel: discord.TextChannel, title: str, minutes: int, mode: str,
    choice1: str, choice2: str, choice3: str = None, choice4: str = None, choice5: str = None,
    choice6: str = None, choice7: str = None, choice8: str = None, choice9: str = None, choice10: str = None
):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return
    if minutes <= 0:
        await interaction.response.send_message("時間は1分以上で指定してください。", ephemeral=True)
        return

    raw_choices = [choice1, choice2, choice3, choice4, choice5, choice6, choice7, choice8, choice9, choice10]
    valid_choices = [c for c in raw_choices if c is not None]
    mode_text = "☝️ 単一選択（1人1票）" if mode == "single" else "🌟 複数選択可能"
    
    poll_embed = discord.Embed(
        title=f"📊 投票：{title}",
        description=f"⏱️ 制限時間: **{minutes}分**\n形式: **{mode_text}**\n\n下のメニューを開いて投票してください！",
        color=discord.Color.blurple()
    )
    poll_embed.set_footer(text=f"投票作成者: {interaction.user.name}")

    try:
        await interaction.response.send_message("投票を作成中...", ephemeral=True)
        poll_message = await channel.send(content="@here", embed=poll_embed)
        max_vals = 1 if mode == "single" else len(valid_choices)
        view = PollView(poll_message.id, valid_choices, max_values=max_vals)
        await poll_message.edit(view=view)
        
        embed_log = discord.Embed(title="📊 コマンドログ: /mo (投票作成)", color=discord.Color.blurple())
        embed_log.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
        embed_log.add_field(name="実行者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
        embed_log.add_field(name="投票形式", value=mode_text, inline=True)
        embed_log.add_field(name="制限時間", value=f"{minutes} 分", inline=True)
        embed_log.add_field(name="投票テーマ", value=title, inline=False)
        await send_channel_log(bot, embed_log)

        asyncio.create_task(poll_timer(interaction.guild.id, channel.id, poll_message.id, minutes, title, valid_choices))
    except discord.Forbidden:
        await channel.send(f"❌ 投票メッセージの送信、またはViewの設置権限がありません。")
    except Exception as e:
        print(f"エラーが発生しました: {e}")

@bot.tree.command(name="chat", description="指定したチャンネルにメッセージを送信します")
@app_commands.describe(channel="メッセージを送信したいチャンネルを選択してください", text="送信する本文を入力してください")
async def chat(interaction: discord.Interaction, channel: discord.TextChannel, text: str):
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(text)
            await interaction.response.send_message(f"#{channel.name} にメッセージを送信しました！", ephemeral=True)
            embed = discord.Embed(title="💬 コマンドログ: /chat", color=discord.Color.green())
            embed.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
            embed.add_field(name="実行者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
            embed.add_field(name="送信先チャンネル", value=f"{channel.mention}", inline=True)
            embed.add_field(name="送信内容", value=text, inline=False)
            await send_channel_log(bot, embed)
        except discord.Forbidden:
            await interaction.response.send_message(f"#{channel.name} への送信権限がありません。", ephemeral=True)
    else:
        await interaction.response.send_message("テキストチャンネルを指定してください。", ephemeral=True)

@bot.tree.command(name="warn", description="ユーザーに警告を付与し、回数に応じて自動で処罰します")
@app_commands.describe(member="警告するユーザーを選択してください", count="付与する警告の個数を入力してください", reason="警告の理由を入力してください")
async def warn(interaction: discord.Interaction, member: discord.Member, count: int, reason: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return
    if count <= 0:
        await interaction.response.send_message("警告数は1以上で指定してください。", ephemeral=True)
        return
    user_id = str(member.id)
    now = datetime.now(timezone.utc)
    new_expire_at = now + timedelta(weeks=3)
    if user_id not in warn_data:
        warn_data[user_id] = {"count": 0, "expire_at": new_expire_at.isoformat(), "logs": []}
    warn_data[user_id]["count"] += count
    warn_data[user_id]["expire_at"] = new_expire_at.isoformat()
    warn_data[user_id]["logs"].append({"count": count, "reason": reason, "date": now.strftime('%Y-%m-%d %H:%M:%S')})
    save_all_data()
    await backup_data_to_discord(bot)
    total_warns = warn_data[user_id]["count"]
    msg = f"⚠️ **ユーザーに警告を与えました**\n**対象者:** {member.mention}\n**今回ついた警告:** {count} 個\n**理由:** {reason}\n**現在の合計警告数:** `{total_warns}` 個\n"
    punishment_msg = ""
    punishment_log_str = "特になし"
    try:
        if total_warns >= 10:
            await member.ban(reason=f"警告合計{total_warns}回に到達のため自動BAN")
            punishment_msg = "🚫 **合計警告数が10個に達したため、サーバーからBANしました。**"
            punishment_log_str = "自動BAN執行"
        elif total_warns >= 7:
            await member.kick(reason=f"警告合計{total_warns}回に到達のため自動キック")
            punishment_msg = "🚷 **合計警告数が7個に達したため、サーバーからキックしました。**"
            punishment_log_str = "自動キック執行"
        elif total_warns >= 5:
            await member.timeout(timedelta(hours=5), reason=f"警告合計{total_warns}回に到達のため自動タイムアウト")
            punishment_msg = "⏱️ **合計警告数が5個に達したため、5時間のタイムアウトを付与しました。**"
            punishment_log_str = "自動タイムアウト (5時間)"
        elif total_warns >= 3:
            await member.timeout(timedelta(hours=3), reason=f"警告合計{total_warns}回に到達のため自動タイムアウト")
            punishment_msg = "⏱️ **合計警告数が3個に達したため、3時間のタイムアウトを付与しました。**"
            punishment_log_str = "自動タイムアウト (3時間)"
    except discord.Forbidden:
        punishment_msg = "\n❌ *権限不足のため処罰を実行できませんでした。*"
        punishment_log_str = "エラー：処罰失敗"
    await interaction.response.send_message(msg + punishment_msg)
    embed = discord.Embed(title="⚠️ コマンドログ: /warn (警告付与)", color=discord.Color.red())
    embed.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
    embed.add_field(name="実行した管理者", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="警告された人", value=f"{member.mention}", inline=True)
    embed.add_field(name="今回商の警告数 / 理由", value=f"`{count}` 個 / {reason}", inline=False)
    embed.add_field(name="現在の合計警告数", value=f"`{total_warns}` 個", inline=True)
    embed.add_field(name="自動処罰結果", value=punishment_log_str, inline=True)
    await send_channel_log(bot, embed)

@bot.tree.command(name="unwarn", description="ユーザーの警告を取り消します")
@app_commands.describe(member="警告を解除したいユーザーを選択してください", amount="消す警告の数を入力してください")
async def unwarn(interaction: discord.Interaction, member: discord.Member, amount: int = None):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return
    user_id = str(member.id)
    if user_id not in warn_data or not warn_data[user_id]["logs"] or warn_data[user_id]["count"] == 0:
        await interaction.response.send_message(f"👤 {member.mention} には消去する警告履歴がありません。", ephemeral=True)
        return
    data = warn_data[user_id]
    total_warnings = data["count"]
    if amount is None or amount >= total_warnings:
        actual_removed = total_warnings
        data["count"] = 0
        data["logs"] = []
        message = f"✅ {member.mention} の警告をすべて削除しました！（計 {actual_removed} 個）"
    else:
        actual_removed = amount
        data["count"] = max(0, data["count"] - amount)
        for _ in range(amount):
            if data["logs"]: data["logs"].pop()
        message = f"✅ {member.mention} の警告を最近のものから {actual_removed} 個削除しました。（残り {data['count']} 個）"
    save_all_data()
    await backup_data_to_discord(bot)
    await interaction.response.send_message(message)
    embed = discord.Embed(title="🍏 コマンドログ: /unwarn (警告解除)", color=discord.Color.gold())
    embed.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
    embed.add_field(name="実行した管理者", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="解除された人", value=f"{member.mention}", inline=True)
    embed.add_field(name="🔧正後の合計警告数", value=f"`{data['count']}` 個", inline=True)
    await send_channel_log(bot, embed)

@bot.tree.command(name="warns", description="指定したユーザーの警告履歴を確認します")
@app_commands.describe(member="警告履歴を見たいユーザーを選択してください")
async def warns(interaction: discord.Interaction, member: discord.Member):
    user_id = str(member.id)
    if user_id not in warn_data or warn_data[user_id]["count"] == 0:
        await interaction.response.send_message(f"👤 {member.mention} には現在、有効な警告はありません。", ephemeral=True)
        return
    data = warn_data[user_id]
    jst_time = datetime.fromisoformat(data["expire_at"]).astimezone(timezone(timedelta(hours=9)))
    expire_str = jst_time.strftime('%Y-%m-%d %H:%M:%S')
    embed = discord.Embed(title=f"⚠️ {member.name} の警告ログ", description=f"**現在の合計警告数:** `{data['count']}` 個\n**全解除される予定日:** {expire_str} (JST)", color=discord.Color.red())
    for i, log in enumerate(data["logs"][:20]):
        embed.add_field(name=f"履歴 #{i + 1} ({log['date']})", value=f"**付与数:** {log['count']}個\n**理由:** {log['reason']}", inline=False)
    await interaction.response.send_message(embed=embed)

TOKEN = os.environ.get('DISCORD_TOKEN')

# ========================================================
# 【絶対死なない無限再接続メインループ】
# ========================================================
async def main():
    while True:
        try:
            async with bot:
                await bot.start(TOKEN)
        except discord.errors.LoginFailure:
            print("【致命的エラー】DISCORD_TOKENが間違っているか無効化されています。")
            break
        except Exception as e:
            print(f"【切断検知】一時的な切断が発生しました。5秒後に自動再接続します: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
