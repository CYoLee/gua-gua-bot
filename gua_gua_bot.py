import os
import json
import base64
import pytz
import discord
from datetime import datetime
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

# === ENV ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_IDS = [int(gid.strip()) for gid in os.getenv("GUILD_IDS", "").split(",")]
tz = pytz.timezone("Asia/Taipei")

# === Firebase Init ===
cred_env = os.getenv("FIREBASE_CREDENTIALS") or ""
try:
    if cred_env.startswith("{"):
        cred_dict = json.loads(cred_env)
    else:
        cred_dict = json.loads(base64.b64decode(cred_env).decode("utf-8"))
except Exception as e:
    raise Exception(f"無法解析 FIREBASE_CREDENTIALS: {e}")
cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# === Discord Init ===
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree  # ← 這裡正確取得指令樹

# === ID 管理 ===
@tree.command(name="add_id", description="新增玩家ID (9位數)")
@app_commands.describe(player_id="玩家 ID")
async def add_id(interaction: discord.Interaction, player_id: str):
    guild_id = str(interaction.guild_id)
    ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
    ref.set({"dummy": "ok"})
    await interaction.response.send_message(f"✅ 已新增 player_id `{player_id}` 至 guild `{guild_id}`", ephemeral=True)

@tree.command(name="remove_id", description="移除玩家ID")
@app_commands.describe(player_id="要移除的玩家 ID")
async def remove_id(interaction: discord.Interaction, player_id: str):
    guild_id = str(interaction.guild_id)
    ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
    ref.delete()
    await interaction.response.send_message(f"✅ 已移除 player_id `{player_id}` 從 guild `{guild_id}`", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID")
async def list_ids(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    docs = db.collection("ids").document(guild_id).collection("players").stream()
    ids = [doc.id for doc in docs]
    if not ids:
        await interaction.response.send_message("📭 沒有任何 ID", ephemeral=True)
    else:
        await interaction.response.send_message(f"📋 玩家 ID 列表：\n- " + "\n- ".join(ids), ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增活動提醒(可多日期或多時間)")
@app_commands.describe(
    date="格式為 YYYY-MM-DD，可輸入多個日期，以逗號分隔",
    time="格式為 HH:MM，可輸入多個時間，以逗號分隔",
    message="要提醒的訊息",
    mention="要標記的對象（例如 @everyone 或 <@&角色ID>）"
)
async def add_notify(interaction: discord.Interaction, date: str, time: str, message: str, mention: str = ""):
    try:
        dates = [d.strip() for d in date.split(",")]
        times = [t.strip() for t in time.split(",")]
        created = 0
        for d in dates:
            for t in times:
                dt = tz.localize(datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M"))
                db.collection("notifications").add({
                    "channel_id": str(interaction.channel_id),
                    "guild_id": str(interaction.guild_id),
                    "datetime": dt.strftime("%Y年%-m月%-d日 %p%-I:%M:00 [UTC+8]"),
                    "message": message,
                    "mention": mention
                })
                created += 1
        await interaction.response.send_message(f"✅ 已新增 {created} 筆提醒", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 新增失敗: {str(e)}", ephemeral=True)

@tree.command(name="list_notify", description="查看提醒列表")
async def list_notify(interaction: discord.Interaction):
    try:
        docs = db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream()
        result = []
        for i, doc in enumerate(docs):
            data = doc.to_dict()
            result.append(f"{i}. `{data.get('datetime')}` - {data.get('message')}")
        if not result:
            await interaction.response.send_message("📭 沒有提醒資料", ephemeral=True)
        else:
            await interaction.response.send_message("\n".join(result), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 讀取失敗: {str(e)}", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒 (index)")
@app_commands.describe(index="欲刪除提醒的 index")
async def remove_notify(interaction: discord.Interaction, index: int):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        if index < 0 or index >= len(docs):
            await interaction.response.send_message("❌ index 無效", ephemeral=True)
            return
        db.collection("notifications").document(docs[index].id).delete()
        await interaction.response.send_message(f"🗑️ 已刪除第 {index} 筆提醒", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 刪除失敗: {str(e)}", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒內容 (by index)")
@app_commands.describe(index="提醒 index", date="新日期", time="新時間", message="新訊息內容", mention="新 mention 對象")
async def edit_notify(interaction: discord.Interaction, index: int, date: str = None, time: str = None, message: str = None, mention: str = None):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        if index < 0 or index >= len(docs):
            await interaction.response.send_message("❌ index 無效", ephemeral=True)
            return
        doc = docs[index]
        data = doc.to_dict()
        orig = datetime.strptime(data["datetime"].split(" ")[0], "%Y年%m月%d日")
        h, m = map(int, data["datetime"].split(" ")[-2].replace(":00", "").split(":"))
        if date:
            y, mo, d = map(int, date.split("-"))
            orig = orig.replace(year=y, month=mo, day=d)
        if time:
            h, m = map(int, time.split(":"))
            orig = orig.replace(hour=h, minute=m)
        data["datetime"] = tz.localize(orig).strftime("%Y年%-m月%-d日 %p%-I:%M:00 [UTC+8]")
        if message:
            data["message"] = message
        if mention:
            data["mention"] = mention
        db.collection("notifications").document(doc.id).update(data)
        await interaction.response.send_message(f"✏️ 已更新第 {index} 筆提醒", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 更新失敗: {str(e)}", ephemeral=True)

# === 推播 Loop ===
@tasks.loop(seconds=30)
async def notify_loop():
    now = datetime.now(tz)
    ts_str = now.strftime("%Y年%-m月%-d日 %p%-I:%M:00 [UTC+8]")
    docs = db.collection("notifications").where("datetime", "==", ts_str).stream()
    for doc in docs:
        data = doc.to_dict()
        channel = bot.get_channel(int(data["channel_id"]))
        if channel:
            try:
                msg = f'{data.get("mention", "")} ⏰ **活動提醒** ⏰\n{data["message"]}'
                await channel.send(msg)
            except Exception as e:
                print(f"[Error] 發送提醒失敗: {e}")
        db.collection("notifications").document(doc.id).delete()

# === Bot Ready ===
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    for gid in GUILD_IDS:
        try:
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
            print(f"✅ Synced commands to guild {gid}")
        except Exception as e:
            print(f"❌ Failed to sync to guild {gid}: {e}")
    notify_loop.start()

bot.run(TOKEN)
