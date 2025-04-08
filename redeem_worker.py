# redeem_worker.py
import os
import discord
import requests
import json
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# Firebase 初始化
cred_json = json.loads(os.environ.get("FIREBASE_CREDENTIALS", "{}"))
if "private_key" in cred_json:
    cred_json["private_key"] = cred_json["private_key"].replace("\\n", "\n")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)

db = firestore.client()
API_URL = os.getenv("REDEEM_API_URL")  # Cloud Run 上的 URL


@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Redeem Bot Ready as {bot.user}")


@tree.command(
    name="redeem", description="輸入兌換碼與 (選填) 單一 ID，否則會自動使用 ids 清單"
)
@app_commands.describe(
    code="兌換碼", player_id="可選，單一 ID，否則會自動使用 Firestore 的 ids 清單"
)
async def redeem(interaction: discord.Interaction, code: str, player_id: str = ""):
    await interaction.response.defer(ephemeral=True)
    batch_id = f"batch-{interaction.id}"

    if not code:
        await interaction.followup.send("❌ 請提供兌換碼", ephemeral=True)
        return

    ids = []
    if player_id:
        ids = [player_id]
    else:
        # 從 Firestore 讀取 ids.txt 清單
        try:
            doc = db.collection("config").document("ids").get()
            data = doc.to_dict()
            ids = data.get("list", [])
        except Exception as e:
            await interaction.followup.send(f"❌ 讀取 ID 清單失敗: {e}", ephemeral=True)
            return

    if not ids:
        await interaction.followup.send("❌ 沒有可用的玩家 ID", ephemeral=True)
        return

    # 發送至 Cloud Run API
    try:
        payload = {"code": code, "ids": ids, "batch_id": batch_id}
        res = requests.post(f"{API_URL}/redeem_submit", json=payload, timeout=15)
        res.raise_for_status()
        result = res.json()
    except Exception as e:
        await interaction.followup.send(f"❌ 呼叫 API 發生錯誤: {e}", ephemeral=True)
        return

    # 彙整結果
    lines = [f"📦 `{code}` Redeem Result"]
    for s in result.get("success", []):
        lines.append(f"✅ {s['player_id']} -> OK")
    for f in result.get("failure", []):
        lines.append(f"❌ {f['player_id']} -> {f.get('reason', 'Fail')}")

    reply = "\n".join(lines)
    await interaction.followup.send(f"```\n{reply}\n```", ephemeral=True)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("❌ DISCORD_TOKEN 環境變數未設定")
    bot.run(token)
