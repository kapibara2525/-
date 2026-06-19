import os
import http.server
import threading
import discord
import asyncio
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone

# ========================================================
# 通知先の設定（NK_banann2JPさんのユーザーID）
# ========================================================
LOG_USER_ID = 1142365276290162788

# ========================================================
# 【Render無料プラン用】自動停止を防ぐためのダミーWebサーバー
# ========================================================
def run_dummy_server():
    port = int(os.environ.get('PORT', 8080))
    server = http.server.HTTPServer(('0.0.0.0', port), http.server.BaseHTTPRequestHandler)
    server.serve_forever()

# 裏側でダミーサーバーを起動
threading.Thread(target=run_dummy_server, daemon=True).start()
print("Render用のダミーサーバーが起動しました。")
# ========================================================

# ボットのインテント設定
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

# 警告データを保存する辞書
warn_data = {}

# ========================================================
# 共通関数：NKさんのDMにログを送信するヘルパー
# ========================================================
async def send_dm_log(bot_instance, embed):
    try:
        user = await bot_instance.fetch_user(LOG_USER_ID)
        if user:
            await user.send(embed=embed)
    except Exception as e:
        print(f"DMログの送信に失敗しました: {e}")

# ========================================================
# 警告の有効期限（3週間）を定期チェックするタスク
# ========================================================
@tasks.loop(minutes=1)
async def check_warn_expiry():
    now = datetime.now(timezone.utc)
    expired_users = []
    
    for user_id, data in warn_data.items():
        if data["count"] > 0 and now >= data["expire_at"]:
            expired_users.append(user_id)
            
    for user_id in expired_users:
        warn_data[user_id]["count"] = 0
        warn_data[user_id]["logs"].append({
            "count": 0,
            "reason": "【システム自動解除】3週間が経過したため警告がすべてリセットされました。",
            "date": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        })
        
        embed = discord.Embed(title="⏰ 警告の自動システム解除通知", color=discord.Color.blue())
        embed.add_field(name="対象ユーザーID", value=f"<@{user_id}> (`{user_id}`)", inline=False)
        embed.add_field(name="内容", value="3週間経過したため、警告カウントが自動リセットされました。", inline=False)
        await send_dm_log(bot, embed)
        
        print(f"ユーザーID: {user_id} の警告が期限切れのためリセットされました。")

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        check_warn_expiry.start()
        await self.tree.sync()
        print("スラッシュコマンドを同期しました。")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name}")

# ========================================================
# 改良機能：/mo コマンド（制限時間・DM通知・順番変更版）
# ========================================================
EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

# バックグラウンドで投票の終了を待つタイマー処理
async def poll_timer(channel_id, message_id, minutes, title, choices_text):
    await asyncio.sleep(minutes * 60) # 指定された「分」の数だけ待機
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        
        # 投票完了時のEmbedを作成（色をグレーに変更）
        end_embed = message.embeds[0]
        end_embed.title = f"🔒 【終了】投票：{title}"
        end_embed.description = "⏰ 設定された時間が経過したため、この投票は締め切られました。"
        end_embed.color = discord.Color.light_grey()
        
        # 投票メッセージを更新して、ついているリアクションをすべて削除
        await message.edit(embed=end_embed)
        await message.clear_reactions()
        
        # NKさんのDMへ終了のログを送信
        embed_log = discord.Embed(title="🔒 コマンドログ: 投票が終了しました", color=discord.Color.light_grey())
        embed_log.add_field(name="投票が行われたチャンネル", value=f"<#{channel_id}>", inline=True)
        embed_log.add_field(name="投票テーマ", value=title, inline=False)
        embed_log.add_field(name="選択肢一覧", value=choices_text, inline=False)
        await send_dm_log(bot, embed_log)
        
    except Exception as e:
        print(f"投票の自動締め切り処理でエラーが発生しました: {e}")

@bot.tree.command(name="mo", description="指定したチャンネルに時間制限付きの投票を作成します")
@app_commands.describe(
    channel="投票を出したいチャンネルを選択してください",
    title="投票の本文（タイトル）を入力してください",
    minutes="投票の制限時間を【分】で入力してください（例: 5）",
    choice1="選択肢1（必須）",
    choice2="選択肢2（必須）",
    choice3="選択肢3（任意）",
    choice4="選択肢4（任意）",
    choice5="選択肢5（任意）"
)
async def mo(
    interaction: discord.Interaction, 
    channel: discord.TextChannel, 
    title: str, 
    minutes: int,
    choice1: str, 
    choice2: str, 
    choice3: str = None, 
    choice4: str = None, 
    choice5: str = None
):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません（メッセージの管理権限が必要です）。", ephemeral=True)
        return

    if minutes <= 0:
        await interaction.response.send_message("時間は1分以上で指定してください。", ephemeral=True)
        return

    raw_choices = [choice1, choice2, choice3, choice4, choice5]
    valid_choices = [c for c in raw_choices if c is not None]

    # 投票用Embedを作成
    poll_embed = discord.Embed(
        title=f"📊 投票：{title}",
        description=f"⏱️ 制限時間: **{minutes}分** (時間が来ると自動で締め切られます)\n下のリアクションを押して投票してください！",
        color=discord.Color.blurple()
    )
    poll_embed.set_footer(text=f"投票作成者: {interaction.user.name}")

    log_choices_text = ""
    for i, choice in enumerate(valid_choices):
        poll_embed.add_field(name=f"{EMOJIS[i]} {choice}", value=" ", inline=False)
        log_choices_text += f"{EMOJIS[i]} {choice}\n"

    try:
        poll_message = await channel.send(embed=poll_embed)
        
        for i in range(len(valid_choices)):
            await poll_message.add_reaction(EMOJIS[i])
            
        await interaction.response.send_message(f"#{channel.name} に投票を作成しました！（制限時間: {minutes}分）", ephemeral=True)

        # NKさんのDMへ開始ログを送信
        embed_log = discord.Embed(title="📊 コマンドログ: /mo (投票作成)", color=discord.Color.blurple())
        embed_log.add_field(name="実行者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
        embed_log.add_field(name="送信先チャンネル", value=f"{channel.mention}", inline=True)
        embed_log.add_field(name="制限時間", value=f"{minutes} 分", inline=True)
        embed_log.add_field(name="投票テーマ", value=title, inline=False)
        embed_log.add_field(name="選択肢一覧", value=log_choices_text, inline=False)
        await send_dm_log(bot, embed_log)

        # 非同期タイマーを裏側で起動
        asyncio.create_task(poll_timer(channel.id, poll_message.id, minutes, title, log_choices_text))

    except discord.Forbidden:
        await interaction.response.send_message(f"#{channel.name} への権限がありません。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)

# ========================================================
# 既存の /chat コマンド
# ========================================================
@bot.tree.command(name="chat", description="指定したチャンネルにメッセージを送信します")
@app_commands.describe(
    channel="メッセージを送信したいチャンネルを選択してください",
    text="送信する本文を入力してください"
)
async def chat(interaction: discord.Interaction, channel: discord.TextChannel, text: str):
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(text)
            await interaction.response.send_message(f"#{channel.name} にメッセージを送信しました！", ephemeral=True)
            
            embed = discord.Embed(title="💬 コマンドログ: /chat", color=discord.Color.green())
            embed.add_field(name="実行者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
            embed.add_field(name="送信先チャンネル", value=f"{channel.mention}", inline=True)
            embed.add_field(name="送信内容", value=text, inline=False)
            await send_dm_log(bot, embed)

        except discord.Forbidden:
            await interaction.response.send_message(f"#{channel.name} への送信権限がありません。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)
    else:
        await interaction.response.send_message("テキストチャンネルを指定してください。", ephemeral=True)

# ========================================================
# 既存の /warn コマンド
# ========================================================
@bot.tree.command(name="warn", description="ユーザーに警告を付与し、回数に応じて自動で処罰します")
@app_commands.describe(
    member="警告するユーザーを選択してください",
    count="付与する警告の個数を入力してください",
    reason="警告の理由を入力してください"
)
async def warn(interaction: discord.Interaction, member: discord.Member, count: int, reason: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません（メッセージの管理権限が必要です）。", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("警告数は1以上で指定してください。", ephemeral=True)
        return

    user_id = member.id
    now = datetime.now(timezone.utc)
    new_expire_at = now + timedelta(weeks=3)

    if user_id not in warn_data:
        warn_data[user_id] = {"count": 0, "expire_at": new_expire_at, "logs": []}
    
    warn_data[user_id]["count"] += count
    warn_data[user_id]["expire_at"] = new_expire_at
    
    current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
    warn_data[user_id]["logs"].append({
        "count": count,
        "reason": reason,
        "date": current_time_str
    })

    total_warns = warn_data[user_id]["count"]

    msg = (
        f"⚠️ **ユーザーに警告を与えました**\n"
        f"**対象者:** {member.mention}\n"
        f"**今回ついた警告:** {count} 個\n"
        f"**理由:** {reason}\n"
        f"**現在の合計警告数:** `{total_warns}` 個\n"
    )

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
        punishment_msg = "\n❌ *ボットの権限が足りないため、自動処罰を実行できませんでした。*"
        punishment_log_str = "エラー：ボットの権限不足により処罰失敗"

    await interaction.response.send_message(msg + punishment_msg)

    embed = discord.Embed(title="⚠️ コマンドログ: /warn (警告付与)", color=discord.Color.red())
    embed.add_field(name="実行した管理者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
    embed.add_field(name="警告された人", value=f"{member.mention} ({member.name})", inline=True)
    embed.add_field(name="今回の警告数 / 理由", value=f"`{count}` 個 / {reason}", inline=False)
    embed.add_field(name="現在の合計警告数", value=f"`{total_warns}` 個", inline=True)
    embed.add_field(name="自動処罰の実行結果", value=punishment_log_str, inline=True)
    await send_dm_log(bot, embed)

# ========================================================
# 既存の /unwarn コマンド
# ========================================================
@bot.tree.command(name="unwarn", description="ユーザーの警告を取り消します")
@app_commands.describe(
    member="警告を解除したいユーザーを選択してください",
    amount="消す警告の数を入力してください（省略するとすべて消します）"
)
async def unwarn(interaction: discord.Interaction, member: discord.Member, amount: int = None):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません（メッセージの管理権限が必要です）。", ephemeral=True)
        return

    user_id = member.id
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
            if data["logs"]:
                data["logs"].pop()
        message = f"✅ {member.mention} の警告を最近のものから {actual_removed} 個削除しました。（残り {data['count']} 個）"

    await interaction.response.send_message(message)

    embed = discord.Embed(title="🍏 コマンドログ: /unwarn (警告解除)", color=discord.Color.gold())
    embed.add_field(name="実行した管理者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
    embed.add_field(name="解除された人", value=f"{member.mention} ({member.name})", inline=True)
    embed.add_field(name="削除した警告数", value=f"`{actual_removed}` 個", inline=True)
    embed.add_field(name="修正後の合計警告数", value=f"`{data['count']}` 個", inline=True)
    await send_dm_log(bot, embed)

# ========================================================
# 既存の /warns コマンド
# ========================================================
@bot.tree.command(name="warns", description="指定したユーザーの警告履歴を確認します")
@app_commands.describe(member="警告履歴を見たいユーザーを選択してください")
async def warns(interaction: discord.Interaction, member: discord.Member):
    user_id = member.id
    
    if user_id not in warn_data or warn_data[user_id]["count"] == 0:
        await interaction.response.send_message(f"👤 {member.mention} には現在、有効な警告はありません。", ephemeral=True)
        return

    data = warn_data[user_id]
    total_warns = data["count"]
    expire_jst = data["expire_at"] + timedelta(hours=9)
    expire_str = expire_jst.strftime('%Y-%m-%d %H:%M:%S')

    embed = discord.Embed(
        title=f"⚠️ {member.name} の警告ログ",
        description=f"**現在の合計警告数:** `{total_warns}` 個\n**全解除される予定日:** {expire_str} (JST)",
        color=discord.Color.red()
    )

    for i, log in enumerate(data["logs"]):
        if i >= 20:
            break
        embed.add_field(
            name=f"履歴 #{i + 1} ({log['date']})",
            value=f"**付与数:** {log['count']}個\n**理由:** {log['reason']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

# ========================================================
# 起動
# ========================================================
TOKEN = os.environ.get('DISCORD_TOKEN')
bot.run(TOKEN)
