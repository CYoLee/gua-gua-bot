# gua_gua_bot.py
import os
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
REDEEM_API_URL = os.getenv("REDEEM_API_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync: {e}")

# ========== /redeem_submit ==========
@tree.command(name="redeem_submit", description="Submit gift code / 提交兌換碼（呱呱雲腳功能）", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    code="兌換碼（必填）",
    player_id="玩家 ID（選填，填了就單人，不填就會用 list_ids 自動多人）"
)
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        payload = {"code": code}
        if player_id:
            payload["player_id"] = player_id
        else:
            payload["guild_id"] = str(interaction.guild_id)
        async with session.post(f"{REDEEM_API_URL}/redeem_submit", json=payload) as resp:
            result = await resp.json()

    if isinstance(result, dict):
        msg = f"🎯 玩家 {result['player_id']}：{'✅ 成功' if result['success'] else '❌ 失敗'}\n📩 {result.get('message') or result.get('reason')}"
    else:
        msg = "🎯 多人兌換結果：\n" + "\n".join(
            f"• {r['player_id']}：{'✅' if r['success'] else '❌'} {r.get('message') or r.get('reason')}" for r in result
        )
    await interaction.followup.send(msg, ephemeral=True)

# ========== /add_id ==========
@tree.command(name="add_id", description="Add a player ID (9 digits) / 新增玩家ID(9位數)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(player_id="要新增的玩家 ID")
async def add_id(interaction: discord.Interaction, player_id: str):
    await interaction.response.defer(ephemeral=True)
    payload = {
        "guild_id": str(interaction.guild_id),
        "player_id": player_id
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{REDEEM_API_URL}/add_id", json=payload) as resp:
            result = await resp.json()
    await interaction.followup.send(result.get("message", "❌ 新增失敗"), ephemeral=True)

# ========== /remove_id ==========
@tree.command(name="remove_id", description="Remove a player ID / 移除玩家ID", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(player_id="要移除的玩家 ID")
async def remove_id(interaction: discord.Interaction, player_id: str):
    await interaction.response.defer(ephemeral=True)
    payload = {
        "guild_id": str(interaction.guild_id),
        "player_id": player_id
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{REDEEM_API_URL}/remove_id", json=payload) as resp:
            result = await resp.json()
    await interaction.followup.send(result.get("message", "❌ 移除失敗"), ephemeral=True)

# ========== /list_ids ==========
@tree.command(name="list_ids", description="List all player IDs stored / 列出所有玩家ID", guild=discord.Object(id=GUILD_ID))
async def list_ids(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{REDEEM_API_URL}/list_ids?guild_id={interaction.guild_id}") as resp:
            result = await resp.json()
    ids = result.get("player_ids", [])
    if ids:
        msg = f"📋 共有 {len(ids)} 位玩家：\n" + "\n".join(f"• `{pid}`" for pid in ids)
    else:
        msg = "⚠️ 尚未新增任何玩家 ID"
    await interaction.followup.send(msg, ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
