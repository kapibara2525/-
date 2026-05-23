import os
import http.server
import threading
import discord
from discord import app_commands
from discord.ext import commands

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

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    # ボット起動時にスラッシュコマンドをサーバーに同期する
    async def setup_hook(self):
        await self.tree.sync()
        print("スラッシュコマンドを同期しました。")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name}")

# /chat コマンドの定義
@bot.tree.command(name="chat", description="指定したチャンネルにメッセージを送信します")
@app_commands.describe(
    channel="メッセージを送信したいチャンネルを選択してください",
    text="送信する本文を入力してください"
)
async def chat(interaction: discord.Interaction, channel: discord.abc.GuildChannel, text: str):
    # 送信先がテキストチャンネル（またはスレッドなど）か確認
    if isinstance(channel, discord.TextChannel):
        try:
            # 指定されたチャンネルにメッセージを送信
            await channel.send(text)
            # コマンドを実行した人にだけ見える内緒の応答
            await interaction.response.send_message(f"#{channel.name} にメッセージを送信しました！", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"#{channel.name} への送信権限がありません。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)
    else:
        await interaction.response.send_message("テキストチャンネルを指定してください。", ephemeral=True)

import os
TOKEN = os.environ.get(“DISCORD_TOKEN”)

bot.run(TOKEN)
