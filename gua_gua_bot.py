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
import aiohttp

# === ENV ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
REDEEM_API_URL = os.getenv("REDEEM_API_URL")
tz = pytz.timezone("Asia/Taipei")

# === Firebase Init ===
cred_env = os.getenv("FIREBASE_CREDENTIALS") or ""
cred_dict = json.loads(base64.b64decode(cred_env).decode("utf-8")) if not cred_env.startswith("{") else json.loads(cred_env)
cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# === Discord Init ===
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# === ID 管理 ===
@tree.command(name="add_id", description="新增玩家ID")
@app_commands.describe(player_id="玩家 ID（9碼數字）")
async def add_id(interaction: discord.Interaction, player_id: str):
    guild_id = str(interaction.guild_id)
    db.collection("ids").document(guild_id).collection("players").document(player_id).set({})
    await interaction.response.send_message(f"✅ 已新增 player_id `{player_id}`", ephemeral=True)

@tree.command(name="remove_id", description="移除玩家ID")
@app_commands.describe(player_id="要移除的 ID")
async def remove_id(interaction: discord.Interaction, player_id: str):
    guild_id = str(interaction.guild_id)
    db.collection("ids").document(guild_id).collection("players").document(player_id).delete()
    await interaction.response.send_message(f"✅ 已移除 player_id `{player_id}`", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID")
async def list_ids(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    docs = db.collection("ids").document(guild_id).collection("players").stream()
    ids = [doc.id for doc in docs]
    await interaction.response.send_message("📋 玩家 ID：\n- " + "\n- ".join(ids) if ids else "📭 沒有任何 ID", ephemeral=True)

# === Redeem 兌換 ===
@tree.command(name="redeem_submit", description="提交兌換碼")
@app_commands.describe(code="兌換碼", player_id="玩家 ID（選填）")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    guild_id = str(interaction.guild_id)
    payload = {"code": code, "guild_id": guild_id}
    if player_id:
        payload["player_id"] = player_id
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{REDEEM_API_URL}/redeem_submit", json=payload) as resp:
            result = await resp.json()
            await interaction.response.send_message(f"🎁 回應：\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```", ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增提醒")
@app_commands.describe(date="YYYY-MM-DD,可多個", time="HH:MM,可多個", message="提醒訊息", mention="標記對象（可空）")
async def add_notify(interaction: discord.Interaction, date: str, time: str, message: str, mention: str = ""):
    dates = [d.strip() for d in date.split(",")]
    times = [t.strip() for t in time.split(",")]
    count = 0
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
            count += 1
    await interaction.response.send_message(f"✅ 已新增 {count} 筆提醒", ephemeral=True)

@tree.command(name="list_notify", description="查看提醒列表")
async def list_notify(interaction: discord.Interaction):
    docs = db.collection("notifications").where(filter=("guild_id", "==", str(interaction.guild_id))).order_by("datetime").stream()
    rows = [f"{i}. `{doc.to_dict().get('datetime')}` - {doc.to_dict().get('message')}" for i, doc in enumerate(docs)]
    await interaction.response.send_message("\n".join(rows) if rows else "📭 沒有提醒資料", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒")
@app_commands.describe(index="提醒 index")
async def remove_notify(interaction: discord.Interaction, index: int):
    docs = list(db.collection("notifications").where(filter=("guild_id", "==", str(interaction.guild_id))).order_by("datetime").stream())
    if index < 0 or index >= len(docs):
        await interaction.response.send_message("❌ index 無效", ephemeral=True)
    else:
        db.collection("notifications").document(docs[index].id).delete()
        await interaction.response.send_message(f"🗑️ 已刪除第 {index} 筆提醒", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒")
@app_commands.describe(index="提醒 index", date="新日期 YYYY-MM-DD", time="新時間 HH:MM", message="新訊息", mention="新標記")
async def edit_notify(interaction: discord.Interaction, index: int, date: str = None, time: str = None, message: str = None, mention: str = None):
    docs = list(db.collection("notifications").where(filter=("guild_id", "==", str(interaction.guild_id))).order_by("datetime").stream())
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

# === 幫助說明指令 ===
@tree.command(name="help", description="查看機器人指令說明")
@app_commands.describe(lang="語言 Language（zh / en）")
async def help_command(interaction: discord.Interaction, lang: str = "zh"):
    if lang.lower() == "en":
        content = (
            "**GuaGuaBOT Help (English)**\n"
            "`/add_id` - Add a player ID to the guild list\n"
            "`/remove_id` - Remove a player ID\n"
            "`/list_ids` - List all player IDs\n"
            "`/redeem_submit` - Submit redeem code (single or all IDs)\n"
            "`/add_notify` - Add scheduled reminders\n"
            "`/list_notify` - List all reminders\n"
            "`/remove_notify` - Remove a reminder by index\n"
            "`/edit_notify` - Edit a reminder\n"
        )
    else:
        content = (
            "**呱呱BOT 指令說明（繁體中文）**\n"
            "`/add_id` - 新增玩家 ID\n"
            "`/remove_id` - 移除玩家 ID\n"
            "`/list_ids` - 顯示目前已登錄的所有 ID\n"
            "`/redeem_submit` - 提交兌換碼（可單人或全部）\n"
            "`/add_notify` - 新增活動提醒（可多日多時間）\n"
            "`/list_notify` - 查看所有活動提醒\n"
            "`/remove_notify` - 移除指定提醒\n"
            "`/edit_notify` - 編輯提醒內容\n"
        )
    await interaction.response.send_message(content, ephemeral=True)

# === 通知推播 ===
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
                await channel.send(f'{data.get("mention", "")} ⏰ **活動提醒** ⏰\n{data["message"]}')
            except Exception as e:
                print(f"[Error] 發送提醒失敗: {e}")
        db.collection("notifications").document(doc.id).delete()

# === 上線後同步所有全域指令 ===
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        cmds = await tree.sync()
        print(f"✅ Synced {len(cmds)} global commands")
    except Exception as e:
        print(f"❌ Failed to sync global commands: {e}")
    notify_loop.start()

bot.run(TOKEN)
