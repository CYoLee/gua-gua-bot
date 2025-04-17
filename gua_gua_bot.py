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
tree = bot.tree

# === 指令區 ===
@tree.command(name="redeem_submit", description="提交兌換任務")
@app_commands.describe(code="兌換碼", player_id="玩家 ID（可選）", guild_id="伺服器 ID（可選）")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None, guild_id: str = None):
    import aiohttp
    url = os.getenv("REDEEM_API_URL") + "/redeem_submit"
    payload = {"code": code}
    if player_id:
        payload["player_id"] = player_id
    if guild_id:
        payload["guild_id"] = guild_id
    elif not player_id:
        payload["guild_id"] = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            result = await resp.json()
    await interaction.response.send_message(f"🎁 結果：\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```", ephemeral=True)

@tree.command(name="add_notify", description="新增活動提醒")
@app_commands.describe(date="YYYY-MM-DD，可逗號分隔", time="HH:MM，可逗號分隔", message="提醒內容", mention="要標記的對象（如 @everyone）")
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

@tree.command(name="list_notify", description="查看所有提醒")
async def list_notify(interaction: discord.Interaction):
    docs = db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream()
    result = []
    for i, doc in enumerate(docs):
        data = doc.to_dict()
        result.append(f"{i}. `{data.get('datetime')}` - {data.get('message')}")
    if not result:
        await interaction.response.send_message("📭 沒有提醒資料", ephemeral=True)
    else:
        await interaction.response.send_message("\n".join(result), ephemeral=True)

@tree.command(name="remove_notify", description="刪除提醒 (index)")
@app_commands.describe(index="要刪除的提醒 index")
async def remove_notify(interaction: discord.Interaction, index: int):
    docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
    if index < 0 or index >= len(docs):
        await interaction.response.send_message("❌ index 無效", ephemeral=True)
        return
    db.collection("notifications").document(docs[index].id).delete()
    await interaction.response.send_message(f"🗑️ 已刪除第 {index} 筆提醒", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒 (index)")
@app_commands.describe(index="index", date="新日期", time="新時間", message="新訊息", mention="新 mention")
async def edit_notify(interaction: discord.Interaction, index: int, date: str = None, time: str = None, message: str = None, mention: str = None):
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

# === 自動通知推播 ===
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
                print(f"[提醒發送失敗] {e}")
        db.collection("notifications").document(doc.id).delete()

# === 啟動 BOT ===
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    for gid in GUILD_IDS:
        try:
            synced = await tree.sync(guild=discord.Object(id=gid))
            print(f"✅ Synced {len(synced)} commands to guild {gid}")
        except Exception as e:
            print(f"❌ Sync failed for guild {gid}: {e}")
    notify_loop.start()

bot.run(TOKEN)
