import os
import json
import discord
import aiohttp
from discord import app_commands, Embed
from discord.ext import commands
import firebase_admin
from firebase_admin import firestore
import firebase_config  # 載入 firebase_config.py

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
REDEEM_API_URL = os.environ.get("REDEEM_API_URL")
GUILD_ID = os.environ.get("GUILD_ID")

db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    print(f"✅ Bot 上線：{bot.user} (ID: {bot.user.id})")
    try:
        print("⏳ 同步指令中...")
        await tree.sync()
        print("✅ 全域指令同步成功")

        guild = discord.Object(id=int(GUILD_ID))
        synced = await tree.sync(guild=guild)
        print(f"✅ 指令已同步至 GUILD {GUILD_ID}（共 {len(synced)} 筆）")
    except Exception as e:
        print(f"❌ 指令同步失敗: {e}")


@tree.command(name="redeem", description="輸入兌換碼與 (選填) 單一 ID")
@app_commands.describe(code="禮包兌換碼", player_id="單一 ID（選填）")
async def redeem(interaction: discord.Interaction, code: str, player_id: str = ""):
    await interaction.response.defer(ephemeral=True)
    batch_id = f"batch-{interaction.id}"
    ids = [player_id] if player_id else []

    if not ids:
        try:
            doc = db.collection("config").document("ids").get()
            ids = (doc.to_dict() or {}).get("list", [])
        except Exception as e:
            await interaction.followup.send(f"❌ Firestore 錯誤: {e}", ephemeral=True)
            return

    if not ids:
        await interaction.followup.send("❌ 無可用 ID", ephemeral=True)
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{REDEEM_API_URL}/redeem_submit",
                json={"code": code, "ids": ids, "batch_id": batch_id},
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
    embeds = []

    for s in result.get("success", []):
        lines.append(f"✅ {s['player_id']} 成功")
        if s.get("screenshot"):
            embed = Embed(title=f"{s['player_id']} 成功截圖", url=s["screenshot"])
            embed.set_image(url=s["screenshot"])
            embeds.append(embed)

    for f in result.get("failure", []):
        lines.append(f"❌ {f['player_id']} 失敗 ({f.get('reason', '未知錯誤')})")
        if f.get("screenshot"):
            embed = Embed(title=f"{f['player_id']} 失敗截圖", url=f["screenshot"])
            embed.set_image(url=f["screenshot"])
            embeds.append(embed)

    await interaction.followup.send(
        f"```\n{chr(10).join(lines)}\n```",
        embeds=embeds[:10],  # 最多10個 embed
        ephemeral=True
    )


def start_discord_bot():
    if not DISCORD_TOKEN:
        raise RuntimeError("❌ DISCORD_TOKEN 未設定")
    bot.run(DISCORD_TOKEN)
