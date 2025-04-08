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

# 載入環境變數
load_dotenv()
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
REDEEM_API_URL = os.environ.get("REDEEM_API_URL")
cred_json = json.loads(os.environ.get("FIREBASE_CREDENTIALS", "{}"))
cred_json["private_key"] = cred_json["private_key"].replace("\\n", "\n")

# Firebase 初始化
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Discord Bot 初始化
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


@tree.command(
    name="redeem",
    description="輸入兌換碼與 (選填) 單一 ID，否則使用 Firestore 中的 ID 清單",
)
@app_commands.describe(
    code="禮包兌換碼", player_id="可選擇指定單一 ID 兌換，否則使用 Firestore 所有 ID"
)
async def redeem(interaction: discord.Interaction, code: str, player_id: str = ""):
    await interaction.response.defer(ephemeral=True)
    batch_id = f"batch-{interaction.id}"

    if not code:
        await interaction.followup.send("❌ 請輸入有效兌換碼", ephemeral=True)
        return

    # 取得 ID 列表：單人 or 多人
    ids = []
    if player_id:
        ids = [player_id]
    else:
        try:
            doc = db.collection("config").document("ids").get()
            data = doc.to_dict()
            ids = data.get("list", [])
        except Exception as e:
            await interaction.followup.send(f"❌ 讀取 ID 清單失敗: {e}", ephemeral=True)
            return

    if not ids:
        await interaction.followup.send("❌ 沒有找到任何可用的 ID", ephemeral=True)
        return

    # 傳送到 Cloud Run redeem_submit API
    try:
        payload = {"code": code, "ids": ids, "batch_id": batch_id}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{REDEEM_API_URL}/redeem_submit", json=payload, timeout=30
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"❌ API 回應錯誤: {resp.status}", ephemeral=True
                    )
                    return
                result = await resp.json()
    except Exception as e:
        await interaction.followup.send(f"❌ API 發送失敗: {e}", ephemeral=True)
        return

    # 建立回覆訊息
    lines = [f"📦 `{code}` 兌換結果："]
    for s in result.get("success", []):
        lines.append(f"✅ {s['player_id']} -> OK")
    for f in result.get("failure", []):
        lines.append(f"❌ {f['player_id']} -> {f.get('reason', 'Fail')}")

    await interaction.followup.send(f"```\n{chr(10).join(lines)}\n```", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("❌ 環境變數 DISCORD_TOKEN 未設定")
    bot.run(DISCORD_TOKEN)
