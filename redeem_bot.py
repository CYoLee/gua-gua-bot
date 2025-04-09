# redeem_bot.py
import os
import json
import discord
import aiohttp
from discord import app_commands
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
REDEEM_API_URL = os.environ.get("REDEEM_API_URL")
GUILD_ID = os.environ.get("GUILD_ID")
print(f"[DEBUG] GUILD_ID: {GUILD_ID}")

cred_json = json.loads(os.environ.get("FIREBASE_CREDENTIALS", "{}"))
if "private_key" in cred_json:
    cred_json["private_key"] = cred_json["private_key"].replace("\\n", "\n")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

guild = discord.Object(id=int(GUILD_ID))


@bot.event
async def on_ready():
    print("🚀 on_ready 觸發")
    print(f"✅ Bot 上線：{bot.user}")
    try:
        synced = await tree.sync(guild=guild)
        print(f"✅ 指令已同步至 GUILD {GUILD_ID}（共 {len(synced)} 筆）")
    except Exception as e:
        print(f"❌ 同步指令失敗: {e}")


@tree.command(
    name="redeem",
    description="輸入兌換碼與 (選填) 單一 ID，否則使用 Firestore 中 config.ids 清單",
)
@app_commands.describe(code="禮包兌換碼", player_id="單一 ID（選填）")
async def redeem(interaction: discord.Interaction, code: str, player_id: str = ""):
    await interaction.response.defer(ephemeral=True)
    batch_id = f"batch-{interaction.id}"

    ids = [player_id] if player_id else []
    if not ids:
        try:
            doc = db.collection("config").document("ids").get()
            data = doc.to_dict() or {}
            ids = data.get("list", [])
        except Exception as e:
            await interaction.followup.send(f"❌ Firestore 錯誤: {e}", ephemeral=True)
            return

    if not ids:
        await interaction.followup.send("❌ 無可用 ID", ephemeral=True)
        return

    try:
        payload = {"code": code, "ids": ids, "batch_id": batch_id}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{REDEEM_API_URL}/redeem_submit", json=payload, timeout=30
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"❌ Cloud Run 回應錯誤：{resp.status}", ephemeral=True
                    )
                    return
                result = await resp.json()
    except Exception as e:
        await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)
        return

    lines = [f"📦 `{code}` 兌換結果："]
    for s in result.get("success", []):
        lines.append(f"✅ {s['player_id']} 成功")
    for f in result.get("failure", []):
        lines.append(f"❌ {f['player_id']} 失敗 ({f.get('reason', '未知錯誤')})")

    await interaction.followup.send(f"```\n{chr(10).join(lines)}\n```", ephemeral=True)


def start_discord_bot():
    if not DISCORD_TOKEN:
        raise RuntimeError("❌ DISCORD_TOKEN 未設定")
    bot.run(DISCORD_TOKEN)
