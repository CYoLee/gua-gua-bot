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
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# === ID 管理 ===
@tree.command(name="add_id", description="新增一個或多個玩家 ID / Add one or multiple player IDs")
@app_commands.describe(player_ids="可以用逗號(,)分隔的玩家 ID / Player IDs separated by comma(,)")
async def add_id(interaction: discord.Interaction, player_ids: str):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        ids = [pid.strip() for pid in player_ids.split(",") if pid.strip()]
        success = []
        exists = []
        for pid in ids:
            ref = db.collection("ids").document(guild_id).collection("players").document(pid)
            if ref.get().exists:
                exists.append(pid)
            else:
                ref.set({})
                success.append(pid)
        msg = []
        if success:
            msg.append(f"✅ 已新增 / Added：`{', '.join(success)}`")
        if exists:
            msg.append(f"⚠️ 已存在 / Already exists：`{', '.join(exists)}`")
        if not msg:
            msg = ["⚠️ 沒有有效的 ID 輸入 / No valid ID input"]
        await interaction.followup.send("\n".join(msg), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_id", description="移除玩家ID / Remove a player ID")
@app_commands.describe(player_id="要移除的 ID / ID to remove")
async def remove_id(interaction: discord.Interaction, player_id: str):
    try:
        guild_id = str(interaction.guild_id)
        ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        if ref.get().exists:
            ref.delete()
            msg = f"✅ 已移除 / Removed player_id `{player_id}`"
        else:
            msg = f"❌ 找不到該 ID / ID not found `{player_id}`"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID / Show all player ID list")
async def list_ids(interaction: discord.Interaction):
    try:
        guild_id = str(interaction.guild_id)
        docs = db.collection("ids").document(guild_id).collection("players").stream()
        ids = [doc.id for doc in docs]
        msg = "📋 玩家 ID / Player IDs：\n- " + "\n- ".join(ids) if ids else "📭 沒有任何 ID / No ID found"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

# === Redeem 兌換 ===
@tree.command(name="redeem_submit", description="提交兌換碼 / Submit redeem code")
@app_commands.describe(code="兌換碼 / Redeem code", player_id="玩家 ID（選填） / Player ID (optional)")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        payload = {"code": code, "guild_id": guild_id}
        if player_id:
            payload["player_id"] = player_id

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{REDEEM_API_URL}/redeem_submit", json=payload) as resp:
                try:
                    if resp.headers.get("Content-Type", "").startswith("application/json"):
                        result = await resp.json()
                        msg = result.get("message") or result.get("reason") or "❓ 未知回應 / Unknown response"
                    else:
                        text = await resp.text()
                        msg = f"⚠️ 非 JSON 回應：{text}"
                except Exception as e:
                    msg = f"❌ 發生錯誤：{e}"
                await interaction.followup.send(f"🎁 回應：{msg}", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{e}", ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增提醒 / Add reminder")
@app_commands.describe(date="YYYY-MM-DD, multiple allowed", time="HH:MM, multiple allowed", message="提醒訊息 / Reminder message", mention="標記對象（可空） / Mention target (optional)")
async def add_notify(interaction: discord.Interaction, date: str, time: str, message: str, mention: str = ""):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
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
        await interaction.followup.send(f"✅ 已新增 / Added {count} 筆提醒", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_notify", description="查看提醒列表 / Check reminder list")
async def list_notify(interaction: discord.Interaction):
    try:
        docs = db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream()
        rows = []
        for i, doc in enumerate(docs):
            data = doc.to_dict()
            try:
                # 例：'2025年4月18日 PM5:15:00 [UTC+8]'
                parts = data["datetime"].split(" ")
                if len(parts) < 2:
                    raise ValueError("Invalid datetime format")

                # 日期處理
                dt = datetime.strptime(parts[0], "%Y年%m月%d日")

                # 時間處理：PM5:15:00
                time_str = parts[1]  # "PM5:15:00"
                ampm = "AM" if time_str.startswith("AM") else "PM"
                time_only = time_str.replace("AM", "").replace("PM", "")
                hour, minute, *_ = map(int, time_only.split(":"))

                # 轉換成 24 小時制
                if ampm == "PM" and hour != 12:
                    hour += 12
                if ampm == "AM" and hour == 12:
                    hour = 0

                dt = dt.replace(hour=hour, minute=minute)
                time_str = dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                time_str = "❓ 時間解析錯誤 / Time error"
            rows.append(f"{i+1}. {time_str} - {data.get('message')}")
        await interaction.response.send_message("\n".join(rows) if rows else "📭 沒有提醒資料 / No reminders found", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒 / Remove reminder")
@app_commands.describe(index="提醒編號（從1開始）")
async def remove_notify(interaction: discord.Interaction, index: int):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.response.send_message("❌ index 無效 / Invalid index", ephemeral=True)
            return
        db.collection("notifications").document(docs[real_index].id).delete()
        doc = docs[real_index]
        data = doc.to_dict()
        await interaction.response.send_message(f"🗑️ 已刪除 / Removed reminder #{index}: {data['message']}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒 / Edit reminder")
@app_commands.describe(index="提醒編號（從1開始）", date="新日期 YYYY-MM-DD", time="新時間 HH:MM", message="新訊息", mention="新標記")
async def edit_notify(interaction: discord.Interaction, index: int, date: str = None, time: str = None, message: str = None, mention: str = None):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.response.send_message("❌ index 無效 / Invalid index", ephemeral=True)
            return
        doc = docs[real_index]
        data = doc.to_dict()

        # 正確解析原 datetime（保留時間與日期）
        try:
            dt_text = data["datetime"].split(" [")[0]  # e.g., '2025年4月18日 PM5:15:00'
            orig = datetime.strptime(dt_text, "%Y年%m月%d日 %p%I:%M")  # 不吃秒數更安全
        except Exception:
            orig = datetime.now(tz)

        # 更新欄位
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
        await interaction.response.send_message(f"✏️ 已更新提醒 / Updated reminder #{index}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

# === Help 指令 ===
@tree.command(name="help", description="查看機器人指令說明 / View command help")
@app_commands.describe(lang="選擇語言 / Choose language")
@app_commands.choices(lang=LANG_CHOICES)
async def help_command(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    try:
        if lang.value == "en":
            content = (
                "**GuaGuaBOT Help (English)**\n"
                "`/add_id` - Add one or multiple player IDs (comma-separated)\n"
                "`/remove_id` - Remove a player ID\n"
                "`/list_ids` - List all saved player IDs\n"
                "`/redeem_submit` - Submit a redeem code\n"
                "`/add_notify` - Add reminders (support multiple dates/times)\n"
                "`/list_notify` - View reminder list\n"
                "`/remove_notify` - Remove a reminder\n"
                "`/edit_notify` - Edit a reminder"
            )
        else:
            content = (
                "**呱呱BOT 指令說明（繁體中文）**\n"
                "`/add_id` - 新增一個或多個玩家 ID（用逗號分隔）\n"
                "`/remove_id` - 移除玩家 ID\n"
                "`/list_ids` - 顯示所有已儲存的 ID\n"
                "`/redeem_submit` - 提交兌換碼\n"
                "`/add_notify` - 新增提醒（支援多個日期與時間）\n"
                "`/list_notify` - 查看提醒列表\n"
                "`/remove_notify` - 移除提醒\n"
                "`/edit_notify` - 編輯提醒"
            )
        await interaction.response.send_message(content, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(
            f"❌ 錯誤：{e}\n⚠️ 發送說明時發生錯誤 / Help command failed.", ephemeral=True)

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
                await channel.send(f'{data.get("mention", "")} \n⏰ **活動提醒 / Reminder** ⏰\n{data["message"]}')
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
