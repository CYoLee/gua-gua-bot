
# redeem_bot.py
import os
import json
import discord
import aiohttp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore

# 初始化環境變數
load_dotenv()
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
REDEEM_API_URL = os.environ.get("REDEEM_API_URL")
cred_json = json.loads(os.environ.get("FIREBASE_CREDENTIALS", "{}"))
cred_json["private_key"] = cred_json["private_key"].replace("\n", "\n")

# 初始化 Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Bot 初始化
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} commands.")
    except Exception as e:
        print(f"❌ Sync failed: {e}")


# /redeem 指令
@tree.command(name="redeem", description="使用禮包碼對 Firestore 中的 ID 進行兌換")
@app_commands.describe(code="輸入兌換碼")
async def redeem(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)

    # 讀取 firestore 中 ids
    docs = db.collection("ids").stream()
    ids = [doc.id for doc in docs]

    if not ids:
        await interaction.followup.send(
            "❌ 未找到任何 ID（請先建立 Firestore ids 集合）"
        )
        return

    # 傳送兌換請求到 Cloud Run
    payload = {
        "code": code,
        "ids": ids,
        "batch_id": f"batch-{interaction.id}",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{REDEEM_API_URL}/redeem_submit",
                json=payload,
                timeout=30,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    lines = [f"📦 Redeem `{code}` 執行完成"]
                    for s in result.get("success", []):
                        lines.append(f"✅ {s['player_id']} 成功")
                    for f in result.get("failure", []):
                        lines.append(f"❌ {f['player_id']} 失敗 ({f['reason']})")
                    msg = "\n".join(lines)
                else:
                    msg = f"❌ Cloud Run 回應錯誤：{resp.status}"

        except Exception as e:
            msg = f"❌ 發送失敗：{e}"

    await interaction.followup.send(f"```\n{msg[:1900]}\n```")


# 啟動 Bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("請設定 DISCORD_TOKEN 環境變數")
    bot.run(DISCORD_TOKEN)
