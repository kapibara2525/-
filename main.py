import os
import http.server
import threading
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from datetime import datetime, timedelta, timezone

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

# ボットのインテント設定（メンバー管理のためにモデレーション権限が必要）
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

# 警告データを保存する辞書
# 構造: { user_id: { "count": 合計数, "expire_at": 消滅する日時, "logs": [ { "count": つけた数, "reason": 理由, "date": 日時 } ] } }
warn_data = {}

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    # ボット起動時にスラッシュコマンドをサーバーに同期する
    async def setup_hook(self):
        self.check_warn_expiry.start() # 警告の期限チェックタイマーを起動
        await self.tree.sync()
        print("スラッシュコマンドを同期しました。")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name}")

# ========================================================
# 警告の有効期限（3週間）を定期チェックするタスク（1分ごとに確認）
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
        print(f"ユーザーID: {user_id} の警告が期限切れのためリセットされました。")

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
        except discord.Forbidden:
            await interaction.response.send_message(f"#{channel.name} への送信権限がありません。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)
    else:
        await interaction.response.send_message("テキストチャンネルを指定してください。", ephemeral=True)

# ========================================================
# 新機能：/warn コマンド
# ========================================================
@bot.tree.command(name="warn", description="ユーザーに警告を付与し、回数に応じて自動で処罰します")
@app_commands.describe(
    member="警告するユーザーを選択してください",
    count="付与する警告の個数を入力してください",
    reason="警告の理由を入力してください"
)
async def warn(interaction: discord.Interaction, member: discord.Member, count: int, reason: str):
    # 実行者に「メンバー管理権限」があるか確認
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません（メッセージの管理権限が必要です）。", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("警告数は1以上で指定してください。", ephemeral=True)
        return

    user_id = member.id
    now = datetime.now(timezone.utc)
    # 新しい期限（今から3週間後）
    new_expire_at = now + timedelta(weeks=3)

    # データの初期化または更新
    if user_id not in warn_data:
        warn_data[user_id] = {"count": 0, "expire_at": new_expire_at, "logs": []}
    
    # 警告を追加し、期限を「全員一律で3週間後」に上書きリセット
    warn_data[user_id]["count"] += count
    warn_data[user_id]["expire_at"] = new_expire_at
    
    # ログを記録
    current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
    warn_data[user_id]["logs"].append({
        "count": count,
        "reason": reason,
        "date": current_time_str
    })

    total_warns = warn_data[user_id]["count"]
    
    # 日本時間での期限表示用（+9時間）
    expire_jst = new_expire_at + timedelta(hours=9)
    expire_str = expire_jst.strftime('%Y-%m-%d %H:%M:%S')

    # 基本の応答メッセージを作成
    msg = (
        f"⚠️ **ユーザーに警告を与えました**\n"
        f"**対象者:** {member.mention}\n"
        f"**今回ついた警告:** {count} 個\n"
        f"**理由:** {reason}\n"
        f"**現在の合計警告数:** `{total_warns}` 個\n"
        f"⏳ *もう一度警告されたため、全警告の期限がリセットされました。次回自動解除: {expire_str} (JST)*\n"
    )

    # 警告数に応じた処罰のチェック
    punishment_msg = ""
    try:
        if total_warns >= 10:
            await member.ban(reason=f"警告合計{total_warns}回に到達のため自動BAN")
            punishment_msg = "🚫 **合計警告数が10個に達したため、サーバーからBANしました。**"
        elif total_warns >= 7:
            await member.kick(reason=f"警告合計{total_warns}回に到達のため自動キック")
            punishment_msg = "🚷 **合計警告数が7個に達したため、サーバーからキックしました。**"
        elif total_warns >= 5:
            await member.timeout(timedelta(hours=5), reason=f"警告合計{total_warns}回に到達のため自動タイムアウト")
            punishment_msg = "⏱️ **合計警告数が5個に達したため、5時間のタイムアウトを付与しました。**"
        elif total_warns >= 3:
            await member.timeout(timedelta(hours=3), reason=f"警告合計{total_warns}回に到達のため自動タイムアウト")
            punishment_msg = "⏱️ **合計警告数が3個に達したため、3時間のタイムアウトを付与しました。**"
    except discord.Forbidden:
        punishment_msg = "\n❌ *ボットの権限が足りないため、自動処罰（タイムアウト/キック/BAN）を実行できませんでした。ボットのロールを上に上げてください。*"

    await interaction.response.send_message(msg + punishment_msg)

# ========================================================
# 新機能：/warns コマンド
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

    # 過去の警告履歴を埋め込みに最大25件まで追加
    for i, log in enumerate(reversed(data["logs"])):
        if i >= 20: # スマホで見やすいように直近20件までに制限
            break
        embed.add_field(
            name=f"履歴 #{len(data['logs']) - i} ({log['date']})",
            value=f"**付与数:** {log['count']}個\n**理由:** {log['reason']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

# ========================================================
# 起動
# ========================================================
TOKEN = os.environ.get('DISCORD_TOKEN')
bot.run(TOKEN)
