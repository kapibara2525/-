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

# --- Render用ダミーサーバー ---
class MyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot is running!".encode("utf-8"))
    def log_message(self, format, *args): return

def run_dummy_server():
    port = int(os.environ.get('PORT', 8080))
    server = http.server.HTTPServer(('0.0.0.0', port), MyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- 設定・変数 ---
LOG_CHANNEL_ID = 1518765558160691230
DATA_FILE = "bot_data.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
intents.reactions = True

warn_data = {}
active_polls = {}
xp_data = {}        
xp_enabled = True   
last_xp_time = {}   
auto_announce_channel_id = None
auto_msg_count = 1

def save_all_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"warn_data": warn_data, "xp_data": xp_data, "xp_enabled": xp_enabled, "auto_announce_channel_id": auto_announce_channel_id}, f, ensure_ascii=False, indent=4)
    except Exception as e: print(f"保存失敗: {e}")

async def send_channel_log(bot_instance, embed):
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if channel: await channel.send(embed=embed)
    except Exception: pass

# --- バックアップ・復元システム（バグ修正済） ---
async def backup_data_to_discord(bot_instance):
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if channel:
            payload = {"warn_data": warn_data, "xp_data": xp_data, "xp_enabled": xp_enabled, "auto_announce_channel_id": auto_announce_channel_id}
            json_str = json.dumps(payload, ensure_ascii=False)
            if len(json_str) < 1800:
                await channel.send(f"==BACKUP_START==\n```json\n{json_str}\n```\n==BACKUP_END==")
            else:
                with open("backup_temp.json", "w", encoding="utf-8") as tmp:
                    json.dump(payload, tmp, ensure_ascii=False)
                await channel.send("【バックアップデータ】", file=discord.File("backup_temp.json", filename="bot_backup.json"))
    except Exception as e: print(f"バックアップ失敗: {e}")

async def load_data_from_discord(bot_instance):
    global warn_data, xp_data, xp_enabled, auto_announce_channel_id
    try:
        channel = bot_instance.get_channel(LOG_CHANNEL_ID) or await bot_instance.fetch_channel(LOG_CHANNEL_ID)
        if not channel: return
        print("【システム】Discordからデータを捜索中...")
        async for message in channel.history(limit=100):
            if "==BACKUP_START==" in message.content and message.author.id == bot_instance.user.id:
                try:
                    content = message.content.split("```json\n")[1].split("\n```")[0]
                    loaded = json.loads(content)
                    warn_data, xp_data = loaded.get("warn_data", {}), loaded.get("xp_data", {})
                    xp_enabled = loaded.get("xp_enabled", True)
                    auto_announce_channel_id = loaded.get("auto_announce_channel_id", None)
                    print("✅ テキストから復元完了")
                    save_all_data(); return
                except Exception: continue
            if message.attachments and message.author.id == bot_instance.user.id:
                for att in message.attachments:
                    if att.filename == "bot_backup.json":
                        try:
                            loaded = json.loads((await att.read()).decode("utf-8"))
                            warn_data, xp_data = loaded.get("warn_data", {}), loaded.get("xp_data", {})
                            xp_enabled = loaded.get("xp_enabled", True)
                            auto_announce_channel_id = loaded.get("auto_announce_channel_id", None)
                            print("✅ ファイルから復元完了")
                            save_all_data(); return
                        except Exception: continue
    except Exception as e: print(f"復元エラー: {e}")

# --- ループタスク ---
@tasks.loop(minutes=1)
async def check_warn_expiry():
    try:
        now = datetime.now(timezone.utc)
        expired = [u_id for u_id, d in warn_data.items() if d.get("count", 0) > 0 and now >= datetime.fromisoformat(d["expire_at"])]
        for u_id in expired:
            warn_data[u_id]["count"] = 0
            warn_data[u_id]["logs"].append({"count": 0, "reason": "【自動解除】3週間経過", "date": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')})
            save_all_data(); await backup_data_to_discord(bot)
            emb = discord.Embed(title="⏰ 警告リセット通知", description=f"<@{u_id}> さんの警告が自動リセットされました。", color=discord.Color.blue())
            await send_channel_log(bot, emb)
    except Exception: pass

@tasks.loop(minutes=3)
async def auto_announce_loop():
    global auto_announce_channel_id, auto_msg_count
    if auto_announce_channel_id is None: return
    try:
        channel = bot.get_channel(auto_announce_channel_id) or await bot.fetch_channel(auto_announce_channel_id)
        if channel:
            now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
            await channel.send(f"🤖 **【定期生存確認】** ボットは現在も正常に動作中だよ！ (回数: {auto_msg_count}回目 / 時刻: {now_str})")
            auto_msg_count += 1
    except Exception: pass

class MyBot(commands.Bot):
    def __init__(self): super().__init__(command_prefix="!", intents=intents, heartbeat_timeout=120.0)
    async def setup_hook(self):
        check_warn_expiry.start()
        auto_announce_loop.start()
        asyncio.create_task(self.delayed_sync())
    async def delayed_sync(self):
        await asyncio.sleep(5)
        await load_data_from_discord(self)
        try: await self.tree.sync(); print("スラッシュコマンドを同期しました。")
        except Exception as e: print(f"同期失敗: {e}")

bot = MyBot()

@bot.event
async def on_ready(): print(f"ログインしました: {bot.user.name}")

# --- レベルシステム関数 ---
def get_next_level_xp(level): return level * 100
def should_have_role(level):
    if level >= 500: return 500
    elif level >= 200: return (level // 50) * 50
    elif level >= 100: return (level // 10) * 10
    elif level >= 5: return (level // 5) * 5
    return None

async def check_and_update_level_roles(member, level):
    target_lvl = should_have_role(level)
    prefix = "Level "
    target_role = None
    if target_lvl is not None:
        r_name = f"{prefix}{target_lvl}"
        target_role = discord.utils.get(member.guild.roles, name=r_name)
        if not target_role:
            try: target_role = await member.guild.create_role(name=r_name, color=discord.Color.from_rgb(46, 204, 113), reason="レベル自動作成")
            except discord.Forbidden: return
    try:
        if target_role and target_role not in member.roles: await member.add_roles(target_role)
        for r in member.roles:
            if r.name.startswith(prefix) and (target_role is None or r.id != target_role.id): await member.remove_roles(r)
    except discord.Forbidden: pass

async def add_xp(member, amount):
    global xp_enabled
    if not xp_enabled: return
    u_id = str(member.id)
    if u_id not in xp_data: xp_data[u_id] = {"level": 1, "xp": 0}
    lvl, xp = xp_data[u_id]["level"], xp_data[u_id]["xp"] + amount
    if lvl >= 500: xp_data[u_id] = {"level": 500, "xp": 0}; save_all_data(); return
    leveled_up = False
    while xp >= get_next_level_xp(lvl):
        xp -= get_next_level_xp(lvl); lvl += 1; leveled_up = True
        if lvl >= 500: lvl = 500; xp = 0; break
    xp_data[u_id] = {"level": lvl, "xp": xp}; save_all_data()
    if leveled_up:
        await check_and_update_level_roles(member, lvl)
        await backup_data_to_discord(bot)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    u_id = message.author.id
    now = datetime.now(timezone.utc)
    if u_id not in last_xp_time or now - last_xp_time[u_id] > timedelta(minutes=1):
        last_xp_time[u_id] = now
        await add_xp(message.author, random.randint(10, 25))
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    if str(member.id) not in xp_data:
        xp_data[str(member.id)] = {"level": 1, "xp": 0}; save_all_data(); await backup_data_to_discord(bot)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id or not payload.guild_id: return
    guild = bot.get_guild(payload.guild_id)
    if not guild or not payload.member or payload.member.bot: return
    await add_xp(payload.member, random.randint(2, 5))

# --- スラッシュコマンド群 ---
@bot.tree.command(name="auto", description="3分ごとの定期生存メッセージを設定します")
async def auto(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global auto_announce_channel_id, auto_msg_count
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("サーバー管理権限がありません。", ephemeral=True); return
    if channel is None:
        auto_announce_channel_id = None; save_all_data(); await backup_data_to_discord(bot)
        await interaction.response.send_message("❌ 定期生存メッセージの設定を解除しました。"); return
    auto_announce_channel_id = channel.id
    auto_msg_count = 1; save_all_data(); await backup_data_to_discord(bot)
    await interaction.response.send_message(f"✅ {channel.mention} を定期生存確認の送信先に設定しました！")
    try:
        now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        await channel.send(f"🤖 **【定期生存確認】** /auto によりここがアナウンス先に設定されたよ！ (時刻: {now_str})")
        auto_msg_count += 1
    except Exception: pass

@bot.tree.command(name="rolecreate", description="レベル用の全ロールを自動で一括作成します")
async def rolecreate(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("ロール管理権限がありません。", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    lvls = list(range(5, 100, 5)) + list(range(100, 200, 10)) + list(range(200, 501, 50))
    created, skipped = 0, 0
    try:
        for l in lvls:
            name = f"Level {l}"
            if not discord.utils.get(interaction.guild.roles, name=name):
                await interaction.guild.create_role(name=name, color=discord.Color.from_rgb(46, 204, 113), reason="レベルシステム一括作成")
                created += 1; await asyncio.sleep(0.2)
            else: skipped += 1
        await interaction.followup.send(f"✅ 完了！作成: `{created}` 個 / スキップ: `{skipped}` 個")
    except Exception as e: await interaction.followup.send(f"❌ エラー: {e}")

@bot.tree.command(name="xpmode", description="レベル・XP機能の有効/無効を切り替えます")
@app_commands.choices(mode=[app_commands.Choice(name="🟢 オン (ON)", value="on"), app_commands.Choice(name="🔴 オフ (OFF)", value="off")])
async def xpmode(interaction: discord.Interaction, mode: str):
    global xp_enabled
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("サーバー管理権限がありません。", ephemeral=True); return
    xp_enabled = (mode == "on")
    save_all_data(); await backup_data_to_discord(bot)
    await interaction.response.send_message(f"⚙️ レベルシステムを **{'🟢 有効' if xp_enabled else '🔴 無効'}** にしました。")

@bot.tree.command(name="level", description="レベルとXP情報を確認します")
async def level(interaction: discord.Interaction, member: discord.Member = None):
    tgt = member or interaction.user
    u_id = str(tgt.id)
    if u_id not in xp_data: xp_data[u_id] = {"level": 1, "xp": 0}
    lvl, xp = xp_data[u_id]["level"], xp_data[u_id]["xp"]
    nxt = get_next_level_xp(lvl)
    emb = discord.Embed(title=f"📊 {tgt.display_name} のステータス", color=discord.Color.green())
    emb.add_field(name="レベル", value=f"🆙 **Lv {lvl}** / 500", inline=True)
    if lvl >= 500: emb.add_field(name="XP", value="✨ **MAX**", inline=True)
    else:
        emb.add_field(name="XP", value=f"✨ `{xp}` / `{nxt}`", inline=True)
        bar = "🟩" * int((xp/nxt)*10) + "⬜" * (10 - int((xp/nxt)*10))
        emb.add_field(name="進捗", value=bar, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="levelset", description="指定メンバーのレベルを強制設定します")
async def levelset(interaction: discord.Interaction, member: discord.Member, target_level: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("権限がありません。", ephemeral=True); return
    if not (1 <= target_level <= 500):
        await interaction.response.send_message("1〜500の間で指定してください。", ephemeral=True); return
    xp_data[str(member.id)] = {"level": target_level, "xp": 0}
    save_all_data(); await backup_data_to_discord(bot)
    await check_and_update_level_roles(member, target_level)
    await interaction.response.send_message(f"🔧 {member.mention} を **Level {target_level}** に変更しました。")

# --- 投票システム ---
class PollSelect(discord.ui.Select):
    def __init__(self, message_id, options, max_vals=1):
        super().__init__(placeholder="選択肢を選んで投票...", min_values=1, max_values=max_vals, options=[discord.SelectOption(label=o, value=o) for o in options], custom_id=f"p_{message_id}")
        self.message_id = message_id
    async def callback(self, interaction: discord.Interaction):
        if self.message_id not in active_polls: active_polls[self.message_id] = {}
        active_polls[self.message_id][interaction.user.id] = self.values
        await interaction.response.send_message(f"✅ {', '.join(self.values)} に投票しました！", ephemeral=True)

class PollView(discord.ui.View):
    def __init__(self, message_id, options, max_vals=1):
        super().__init__(timeout=None)
        self.add_item(PollSelect(message_id, options, max_vals))

async def poll_timer(guild_id, channel_id, message_id, minutes, title, choices):
    await asyncio.sleep(minutes * 60)
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        msg = await channel.fetch_message(message_id)
        votes = active_polls.pop(message_id, {})
        counts = {c: [] for c in choices}
        for u_id, c_list in votes.items():
            for c in c_list:
                if c in counts: counts[c].append(f"<@{u_id}>")
        res = "ーー 【集計結果】 ーー\n"
        for c in choices: res += f"🔹 **{c}**: `{len(counts[c])}` 人\n└ {', '.join(counts[c]) if counts[c] else 'なし'}\n"
        emb = msg.embeds[0]; emb.title = f"🔒 【終了】投票：{title}"; emb.description = res; emb.color = discord.Color.light_grey()
        await msg.edit(embed=emb, view=None)
    except Exception: pass

@bot.tree.command(name="mo", description="投票を作成します（最大10択）")
@app_commands.choices(mode=[app_commands.Choice(name="☝️ 単一選択", value="single"), app_commands.Choice(name="🌟 複数選択", value="multiple")])
async def mo(
    interaction: discord.Interaction, channel: discord.TextChannel, title: str, minutes: int, mode: str,
    choice1: str, choice2: str, choice3: str = None, choice4: str = None, choice5: str = None,
    choice6: str = None, choice7: str = None, choice8: str = None, choice9: str = None, choice10: str = None
):
    if not interaction.user.guild_permissions.manage_messages: return
    choices = [c for c in [choice1, choice2, choice3, choice4, choice5, choice6, choice7, choice8, choice9, choice10] if c]
    emb = discord.Embed(title=f"📊 投票：{title}", description=f"⏱️ 制限: {minutes}分\n下から選んで投票してください！", color=discord.Color.blurple())
    await interaction.response.send_message("作成完了", ephemeral=True)
    msg = await channel.send(content="@here", embed=emb)
    view = PollView(msg.id, choices, max_vals=1 if mode=="single" else len(choices))
    await msg.edit(view=view)
    asyncio.create_task(poll_timer(interaction.guild.id, channel.id, msg.id, minutes, title, choices))

@bot.tree.command(name="chat", description="メッセージを送信します")
async def chat(interaction: discord.Interaction, channel: discord.TextChannel, text: str):
    try:
        await channel.send(text)
        await interaction.response.send_message("送信完了", ephemeral=True)
    except Exception: await interaction.response.send_message("送信失敗", ephemeral=True)

# --- 警告システム ---
@bot.tree.command(name="warn", description="警告を付与し自動処罰します")
async def warn(interaction: discord.Interaction, member: discord.Member, count: int, reason: str):
    if not interaction.user.guild_permissions.manage_messages: return
    u_id = str(member.id); now = datetime.now(timezone.utc)
    if u_id not in warn_data: warn_data[u_id] = {"count": 0, "expire_at": (now + timedelta(weeks=3)).isoformat(), "logs": []}
    warn_data[u_id]["count"] += count
    warn_data[u_id]["logs"].append({"count": count, "reason": reason, "date": now.strftime('%Y-%m-%d %H:%M:%S')})
    save_all_data(); await backup_data_to_discord(bot)
    total = warn_data[u_id]["count"]
    msg = f"⚠️ {member.mention} に警告を {count} 個付与。合計: `{total}` 個\n"
    try:
        if total >= 10: await member.ban(reason="警告10回"); msg += "🚫 BAN執行。"
        elif total >= 7: await member.kick(reason="警告7回"); msg += "🚷 キック執行。"
        elif total >= 5: await member.timeout(timedelta(hours=5)); msg += "⏱️ 5時間タイムアウト。"
        elif total >= 3: await member.timeout(timedelta(hours=3)); msg += "⏱️ 3時間タイムアウト。"
    except Exception: msg += "\n❌ 権限不足で処罰スキップ。"
    await interaction.response.send_message(msg)

@bot.tree.command(name="unwarn", description="警告を取り消します")
async def unwarn(interaction: discord.Interaction, member: discord.Member, amount: int = None):
    if not interaction.user.guild_permissions.manage_messages: return
    u_id = str(member.id)
    if u_id not in warn_data or warn_data[u_id]["count"] == 0:
        await interaction.response.send_message("履歴履歴がありません。", ephemeral=True); return
    if amount is None or amount >= warn_data[u_id]["count"]: warn_data[u_id]["count"], warn_data[u_id]["logs"] = 0, []
    else: warn_data[u_id]["count"] -= amount; warn_data[u_id]["logs"] = warn_data[u_id]["logs"][:-amount]
    save_all_data(); await backup_data_to_discord(bot)
    await interaction.response.send_message(f"✅ 解除完了。残り警告: `{warn_data[u_id]['count']}`")

@bot.tree.command(name="warns", description="警告履歴を確認します")
async def warns(interaction: discord.Interaction, member: discord.Member):
    u_id = str(member.id)
    if u_id not in warn_data or warn_data[u_id]["count"] == 0:
        await interaction.response.send_message("有効な警告はありません。", ephemeral=True); return
    d = warn_data[u_id]
    emb = discord.Embed(title=f"⚠️ {member.name} の警告ログ (合計: {d['count']})", color=discord.Color.red())
    for i, log in enumerate(d["logs"][:10]):
        emb.add_field(name=f"#{i+1} ({log['date']})", value=f"個数: {log['count']} / 理由: {log['reason']}", inline=False)
    await interaction.response.send_message(embed=emb)

# --- 無限再接続メインループ ---
TOKEN = os.environ.get('DISCORD_TOKEN')

async def main():
    while True:
        try:
            async with bot: await bot.start(TOKEN)
        except Exception as e:
            print(f"再接続中...: {e}"); await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
