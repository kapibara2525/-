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

threading.Thread(target=run_dummy_server, daemon=True).start()
print("Render用のダミーサーバーが起動しました。")
# ========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

warn_data = {}

# 投票中のデータを一時保存するグローバル辞書
active_polls = {}

async def send_dm_log(bot_instance, embed):
    try:
        user = await bot_instance.fetch_user(LOG_USER_ID)
        if user:
            await user.send(embed=embed)
    except Exception as e:
        print(f"DMログの送信に失敗しました: {e}")

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
# セレクトメニュー方式の投票 UI コンポーネント
# ========================================================
class PollSelect(discord.ui.Select):
    def __init__(self, message_id, options_list, max_values=1):
        super().__init__(
            placeholder="ここをタップして投票する選択肢を選んでください...",
            min_values=1,
            max_values=max_values,
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

# ========================================================
# タイマーと集計処理
# ========================================================
async def poll_timer(guild_id, channel_id, message_id, minutes, title, valid_choices):
    await asyncio.sleep(minutes * 60)
    try:
        guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        
        votes = active_polls.pop(message_id, {})
        choice_counts = {choice: [] for choice in valid_choices}
        total_votes = 0
        
        for user_id, chosen_list in votes.items():
            for chosen in chosen_list:
                if chosen in choice_counts:
                    choice_counts[chosen].append(f"<@{user_id}>")
                    total_votes += 1
                    
        result_text = "ーー 【集計結果】 ーー\n"
        for choice in valid_choices:
            voters = choice_counts[choice]
            count = len(voters)
            if count > 0:
                result_text += f"🔹 **{choice}**: `{count}` 票\n└ 投票者: {', '.join(voters)}\n"
            else:
                result_text += f"🔹 **{choice}**: `0` 票\n└ 投票者: なし\n"
        result_text += f"\n総得票数: `{total_votes}` 票"

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
        await send_dm_log(bot, embed_log)
        
    except Exception as e:
        print(f"投票の自動締め切り処理でエラーが発生しました: {e}")

# ========================================================
# /mo コマンド本体（選択肢10個拡張版）
# ========================================================
@bot.tree.command(name="mo", description="指定したチャンネルにメニュー選択式の投票を作成します（最大10択）")
@app_commands.describe(
    channel="投票を出したいチャンネルを選択してください",
    title="投票の本文（タイトル）を入力してください",
    minutes="投票の制限時間を【分】で入力してください",
    mode="1つだけ選択させるか、複数選択を許可するかを選んでください",
    choice1="選択肢1（必須）", choice2="選択肢2（必須）",
    choice3="選択肢3（任意）", choice4="選択肢4（任意）", choice5="選択肢5（任意）",
    choice6="選択肢6（任意）", choice7="選択肢7（任意）", choice8="選択肢8（任意）",
    choice9="選択肢9（任意）", choice10="選択肢10（任意）"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="☝️ 1つだけ選択可能（単一選択）", value="single"),
    app_commands.Choice(name="🌟 複数選択可能（選べるだけすべて）", value="multiple")
])
async def mo(
    interaction: discord.Interaction, 
    channel: discord.TextChannel, 
    title: str, 
    minutes: int,
    mode: str,
    choice1: str, choice2: str, 
    choice3: str = None, choice4: str = None, choice5: str = None,
    choice6: str = None, choice7: str = None, choice8: str = None,
    choice9: str = None, choice10: str = None
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

    log_choices_text = ""
    for choice in valid_choices:
        poll_embed.add_field(name=f"・ {choice}", value=" ", inline=False)
        log_choices_text += f"・ {choice}\n"

    try:
        await interaction.response.send_message("投票を作成中...", ephemeral=True)
        poll_message = await channel.send(content="@here", embed=poll_embed)
        
        # 複数選択の場合は、入力された有効な選択肢の数を上限（最大10）にする
        max_vals = 1 if mode == "single" else len(valid_choices)
        
        view = PollView(poll_message.id, valid_choices, max_values=max_vals)
        await poll_message.edit(view=view)
        
        embed_log = discord.Embed(title="📊 コマンドログ: /mo (投票作成)", color=discord.Color.blurple())
        embed_log.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
        embed_log.add_field(name="実行者", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
        embed_log.add_field(name="投票形式", value=mode_text, inline=True)
        embed_log.add_field(name="制限時間", value=f"{minutes} 分", inline=True)
        embed_log.add_field(name="投票テーマ", value=title, inline=False)
        await send_dm_log(bot, embed_log)

        asyncio.create_task(poll_timer(interaction.guild.id, channel.id, poll_message.id, minutes, title, valid_choices))

    except discord.Forbidden:
        await channel.send(f"❌ 投票メッセージの送信、またはViewの設置権限がありません。")
    except Exception as e:
        print(f"エラーが発生しました: {e}")

# ========================================================
# 既存の /chat, /warn, /unwarn, /warns コマンド (変更なし)
# ========================================================
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
            await send_dm_log(bot, embed)
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
    user_id = member.id
    now = datetime.now(timezone.utc)
    new_expire_at = now + timedelta(weeks=3)
    if user_id not in warn_data:
        warn_data[user_id] = {"count": 0, "expire_at": new_expire_at, "logs": []}
    warn_data[user_id]["count"] += count
    warn_data[user_id]["expire_at"] = new_expire_at
    warn_data[user_id]["logs"].append({"count": count, "reason": reason, "date": now.strftime('%Y-%m-%d %H:%M:%S')})
    total_warns = warn_data[user_id]["count"]
    msg = f"⚠️ **ユーザーに警告を与えました**\n**対象者:** {member.mention}\n**今回ついた警告:** {count} 個\n**理由:** {reason}\n**現在の合計警告数:** `{total_warns}` 個\n"
    punishment_msg = ""
    punishment_log_str = "特なし"
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
    embed.add_field(name="今回の警告数 / 理由", value=f"`{count}` 個 / {reason}", inline=False)
    embed.add_field(name="現在の合計警告数", value=f"`{total_warns}` 個", inline=True)
    embed.add_field(name="自動処罰結果", value=punishment_log_str, inline=True)
    await send_dm_log(bot, embed)

@bot.tree.command(name="unwarn", description="ユーザーの警告を取り消します")
@app_commands.describe(member="警告を解除したいユーザーを選択してください", amount="消す警告の数を入力してください")
async def unwarn(interaction: discord.Interaction, member: discord.Member, amount: int = None):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
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
            if data["logs"]: data["logs"].pop()
        message = f"✅ {member.mention} の警告を最近のものから {actual_removed} 個削除しました。（残り {data['count']} 個）"
    await interaction.response.send_message(message)
    embed = discord.Embed(title="🍏 コマンドログ: /unwarn (警告解除)", color=discord.Color.gold())
    embed.add_field(name="サーバー名", value=f"**{interaction.guild.name}**", inline=False)
    embed.add_field(name="実行した管理者", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="解除された人", value=f"{member.mention}", inline=True)
    embed.add_field(name="修正後の合計警告数", value=f"`{data['count']}` 個", inline=True)
    await send_dm_log(bot, embed)

@bot.tree.command(name="warns", description="指定したユーザーの警告履歴を確認します")
@app_commands.describe(member="警告履歴を見たいユーザーを選択してください")
async def warns(interaction: discord.Interaction, member: discord.Member):
    user_id = member.id
    if user_id not in warn_data or warn_data[user_id]["count"] == 0:
        await interaction.response.send_message(f"👤 {member.mention} には現在、有効な警告はありません。", ephemeral=True)
        return
    data = warn_data[user_id]
    expire_str = (data["expire_at"] + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')
    embed = discord.Embed(title=f"⚠️ {member.name} の警告ログ", description=f"**現在の合計警告数:** `{data['count']}` 個\n**全解除される予定日:** {expire_str} (JST)", color=discord.Color.red())
    for i, log in enumerate(data["logs"][:20]):
        embed.add_field(name=f"履歴 #{i + 1} ({log['date']})", value=f"**付与数:** {log['count']}個\n**理由:** {log['reason']}", inline=False)
    await interaction.response.send_message(embed=embed)

TOKEN = os.environ.get('DISCORD_TOKEN')
bot.run(TOKEN)
