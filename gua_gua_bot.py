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
LANG_CHOICES = [
    app_commands.Choice(name="繁體中文", value="zh"),
    app_commands.Choice(name="English", value="en"),
]

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
    try:
        guild_id = str(interaction.guild_id)
        ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        if ref.get().exists:
            await interaction.response.send_message(f"⚠️ player_id `{player_id}` 已存在", ephemeral=True)
        else:
            ref.set({})
            await interaction.response.send_message(f"✅ 已新增 player_id `{player_id}`", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_id", description="移除玩家ID")
@app_commands.describe(player_id="要移除的 ID")
async def remove_id(interaction: discord.Interaction, player_id: str):
    try:
        guild_id = str(interaction.guild_id)
        ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        if ref.get().exists:
            ref.delete()
            msg = f"✅ 已移除 player_id `{player_id}`"
        else:
            msg = f"❌ 找不到該 ID `{player_id}`"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID")
async def list_ids(interaction: discord.Interaction):
    try:
        guild_id = str(interaction.guild_id)
        docs = db.collection("ids").document(guild_id).collection("players").stream()
        ids = [doc.id for doc in docs]
        msg = "📋 玩家 ID：\n- " + "\n- ".join(ids) if ids else "📭 沒有任何 ID"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

# === Redeem 兌換 ===
@tree.command(name="redeem_submit", description="提交兌換碼")
@app_commands.describe(code="兌換碼", player_id="玩家 ID（選填）")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        payload = {"code": code, "guild_id": guild_id}
        if player_id:
            payload["player_id"] = player_id
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{REDEEM_API_URL}/redeem_submit", json=payload) as resp:
                result = await resp.json()
                await interaction.followup.send(
                    f"🎁 回應：\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```",
                    ephemeral=True
                )
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{e}", ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增提醒")
@app_commands.describe(date="YYYY-MM-DD,可多個", time="HH:MM,可多個", message="提醒訊息", mention="標記對象（可空）")
async def add_notify(interaction: discord.Interaction, date: str, time: str, message: str, mention: str = ""):
    try:
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
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_notify", description="查看提醒列表")
async def list_notify(interaction: discord.Interaction):
    try:
        docs = db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream()
        rows = [f"{i+1}. `{doc.to_dict().get('datetime')}` - {doc.to_dict().get('message')}" for i, doc in enumerate(docs)]
        await interaction.response.send_message("\n".join(rows) if rows else "📭 沒有提醒資料", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒")
@app_commands.describe(index="提醒編號（從1開始）")
async def remove_notify(interaction: discord.Interaction, index: int):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.response.send_message("❌ index 無效", ephemeral=True)
            return
        db.collection("notifications").document(docs[real_index].id).delete()
        await interaction.response.send_message(f"🗑️ 已刪除第 {index} 筆提醒", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒")
@app_commands.describe(index="提醒編號（從1開始）", date="新日期 YYYY-MM-DD", time="新時間 HH:MM", message="新訊息", mention="新標記")
async def edit_notify(interaction: discord.Interaction, index: int, date: str = None, time: str = None, message: str = None, mention: str = None):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.response.send_message("❌ index 無效", ephemeral=True)
            return
        doc = docs[real_index]
        data = doc.to_dict()
        # 重建 datetime（簡化為只解析 date & time，不使用原 datetime 拆 AM/PM）
        orig = datetime.strptime(data["datetime"].split(" ")[0], "%Y年%m月%d日")
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
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

# === Help 指令 ===
@tree.command(name="help", description="查看機器人指令說明")
@app_commands.describe(lang="選擇語言")
@app_commands.choices(lang=LANG_CHOICES)
async def help_command(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    try:
        if lang.value == "en":
            content = (
                "**GuaGuaBOT Help (English)**\n"
                "`/add_id` - Add a player ID\n"
                "`/remove_id` - Remove a player ID\n"
                "`/list_ids` - List player IDs\n"
                "`/redeem_submit` - Submit redeem code\n"
                "`/add_notify` - Add reminder\n"
                "`/list_notify` - List reminders\n"
                "`/remove_notify` - Remove reminder\n"
                "`/edit_notify` - Edit reminder"
            )
        else:
            content = (
                "**呱呱BOT 指令說明（繁體中文）**\n"
                "`/add_id` - 新增玩家 ID\n"
                "`/remove_id` - 移除玩家 ID\n"
                "`/list_ids` - 顯示所有 ID\n"
                "`/redeem_submit` - 提交兌換碼\n"
                "`/add_notify` - 新增提醒\n"
                "`/list_notify` - 查看提醒\n"
                "`/remove_notify` - 移除提醒\n"
                "`/edit_notify` - 編輯提醒"
            )
        await interaction.response.send_message(content, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

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

# === 上線後同步 ===
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        cmds = await tree.sync()
        print(f"✅ Synced {len(cmds)} global commands")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    if not notify_loop.is_running():
        notify_loop.start()

bot.run(TOKEN)
