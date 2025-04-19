import os
import json
import base64
import pytz
import discord
import re
from datetime import datetime, timedelta
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
import re

@tree.command(name="add_id", description="新增一個或多個玩家 ID / Add one or multiple player IDs")
@app_commands.describe(player_ids="可以用逗號(,)分隔的玩家 ID / Player IDs separated by comma(,)")
async def add_id(interaction: discord.Interaction, player_ids: str):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        ids = [pid.strip() for pid in player_ids.split(",") if pid.strip()]

        # 驗證每個玩家 ID 是否為 9 位數字
        valid_ids = []
        invalid_ids = []
        for pid in ids:
            if re.match(r'^\d{9}$', pid):  # 檢查是否為 9 位數字
                valid_ids.append(pid)
            else:
                invalid_ids.append(pid)

        if invalid_ids:
            msg = f"⚠️ 無效 ID（非 9 位數字）：`{', '.join(invalid_ids)}`"
            await interaction.followup.send(msg, ephemeral=True)
            return

        success = []
        exists = []
        for pid in valid_ids:
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
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID / Show all player ID list")
async def list_ids(interaction: discord.Interaction):
    try:
        guild_id = str(interaction.guild_id)
        docs = db.collection("ids").document(guild_id).collection("players").stream()
        ids = [doc.id for doc in docs]
        msg = "📋 玩家 ID / Player IDs：\n- " + "\n- ".join(ids) if ids else "📭 沒有任何 ID / No ID found"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

# === Redeem 兌換 ===
@tree.command(name="redeem_submit", description="提交兌換碼 / Submit redeem code")
@app_commands.describe(code="兌換碼 / Redeem code", player_id="玩家 ID（選填） / Player ID (optional)")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    try:
        # 開始執行之前告訴用戶正在處理
        await interaction.response.send_message("🎁 兌換處理中，請稍候...此過程可能需要一些時間，請勿重複提交\n# Redeem is being processed, please wait... This may take some time, please do not submit again.", ephemeral=True)
        
        guild_id = str(interaction.guild_id)
        payload = {"code": code, "guild_id": guild_id}
        if player_id:
            payload["player_id"] = player_id

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{REDEEM_API_URL}/redeem_submit", json=payload) as resp:
                try:
                    if resp.headers.get("Content-Type", "").startswith("application/json"):
                        result = await resp.json()
                        print(f"[Debug] Cloud Run 回傳結果：{result}")  # Debug log
                        if not isinstance(result, dict):
                            msg = f"⚠️ 非預期格式：{result}\n# Unexpected format: {result}"
                            await interaction.followup.send(msg, ephemeral=True)
                            return
                    else:
                        text = await resp.text()
                        await interaction.followup.send(f"⚠️ 非 JSON 回應：{text}\n# Non-JSON response: {text}", ephemeral=True)
                        return
                except Exception as e:
                    await interaction.followup.send(f"❌ 發生錯誤：{str(e)}\n# Error occurred: {str(e)}", ephemeral=True)
                    return

        # === 檢查 result 是否為空，若空則顯示錯誤訊息 ===
        if not result.get("success") and not result.get("fails"):
            await interaction.followup.send("⚠️ 沒有收到任何成功或失敗結果，請確認後端是否正常處理\n# No success or failure results received, please check if the backend is processing correctly.", ephemeral=True)
            return

        # === 格式化訊息 ===
        msg_lines = [result.get("message", "🎁 兌換結果如下\n# Redeem results as follows").strip() or "🎁 兌換結果如下\n# Redeem results as follows"]

        # 顯示成功玩家
        success_ids = [item.get("player_id", "未知ID") for item in result.get("success", [])]
        if success_ids:
            msg_lines.append(f"✅ 成功 ID: {', '.join(success_ids)}\n# Success IDs: {', '.join(success_ids)}")

        # 分批顯示失敗玩家，避免字數過長
        fail_ids = [item.get("player_id", "未知ID") for item in result.get("fails", [])]
        if fail_ids:
            batch_size = 20  # 每批顯示 20 位失敗玩家
            for i in range(0, len(fail_ids), batch_size):
                batch = fail_ids[i:i + batch_size]
                fail_msg = f"❌ 失敗 ID: {', '.join(batch)}\n# Failure IDs: {', '.join(batch)}"
                msg_lines.append(fail_msg)

        # 確保每條訊息長度不超過 2000 字符
        full_message = "\n".join(msg_lines)

        if len(full_message) > 2000:
            await interaction.followup.send(
                f"{result['message']}\n⚠️ 成功/失敗名單過長，已略過細節（請改用少量 ID 或查看伺服器日誌）\n# Success/Failure list too long, details skipped (please use fewer IDs or check the server logs).",
                ephemeral=True
            )
        else:
            await interaction.followup.send(full_message, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{e}\n# Error occurred: {e}", ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增提醒 / Add reminder")
@app_commands.describe(
    date="YYYY-MM-DD, multiple allowed",
    time="HH:MM, multiple allowed",
    message="提醒訊息 / Reminder message",
    mention="標記對象（可空） / Mention target (optional)",
    target_channel="提醒要送出的頻道（可選）"
)
async def add_notify(
    interaction: discord.Interaction,
    date: str,
    time: str,
    message: str,
    mention: str = "",
    target_channel: discord.TextChannel = None
):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        dates = [d.strip() for d in date.split(",")]
        times = [t.strip() for t in time.split(",")]
        count = 0
        for d in dates:
            for t in times:
                dt = tz.localize(datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M"))
                db.collection("notifications").add({
                    "channel_id": str(target_channel.id if target_channel else interaction.channel_id),
                    "guild_id": str(interaction.guild_id),
                    "datetime": dt,
                    "message": message.replace("\\n", "\n"),
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
                dt = data["datetime"].astimezone(tz)
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                time_str = "❓ 時間解析錯誤 / Time error"
            rows.append(f"{i+1}. {time_str} - {data.get('message')}")
        await interaction.response.send_message("\n".join(rows) if rows else "📭 沒有提醒資料 / No reminders found", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒 / Remove reminder")
@app_commands.describe(index="提醒編號（從1開始）")
async def remove_notify(interaction: discord.Interaction, index: int):
    try:
        docs = list(db.collection("notifications").where("guild_id", "==", str(interaction.guild_id)).order_by("datetime").stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.response.send_message("❌ index 無效 / Invalid index", ephemeral=True)
            return
        doc = docs[real_index]
        data = doc.to_dict()
        db.collection("notifications").document(doc.id).delete()
        await interaction.response.send_message(f"🗑️ 已刪除 / Removed reminder #{index}: {data['message']}", ephemeral=True)

        # 推送到監控頻道
        log_channel = bot.get_channel(1356431597150408786)
        if log_channel:
            await log_channel.send(
                f"🗑️ **提醒被刪除**\n"
                f"👤 操作者：{interaction.user} ({interaction.user.id})\n"
                f"🌐 伺服器：{interaction.guild.name} ({interaction.guild.id})\n"
                f"📌 原提醒：{data['datetime']} - {data['message']}"
            )

    except Exception as e:
        await interaction.response.send_message(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒 / Edit reminder")
@app_commands.describe(
    index="提醒編號（從1開始）",
    date="新日期 YYYY-MM-DD",
    time="新時間 HH:MM",
    message="新訊息",
    mention="新標記",
    target_channel="提醒要送出的頻道（可選）"
)
async def edit_notify(
    interaction: discord.Interaction,
    index: int,
    date: str = None,
    time: str = None,
    message: str = None,
    mention: str = None,
    target_channel: discord.TextChannel = None
):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)

        docs = list(db.collection("notifications")
                    .where("guild_id", "==", str(interaction.guild_id))
                    .order_by("datetime")
                    .stream())
        real_index = index - 1
        if real_index < 0 or real_index >= len(docs):
            await interaction.followup.send("❌ index 無效 / Invalid index", ephemeral=True)
            return

        doc = docs[real_index]
        old_data = doc.to_dict()

        # === 原時間解析（修正 Timestamp 為標準 datetime）===
        try:
            firestore_dt = old_data["datetime"]
            orig = datetime.fromtimestamp(firestore_dt.timestamp(), tz)
        except Exception:
            await interaction.followup.send("❌ 原時間格式解析失敗，無法修改", ephemeral=True)
            return

        # === 修改時間 ===
        if date:
            y, mo, d = map(int, date.split("-"))
            orig = orig.replace(year=y, month=mo, day=d)
        if time:
            h, m = map(int, time.split(":"))
            orig = orig.replace(hour=h, minute=m)

        # === 套用時區（保險起見） ===
        if orig.tzinfo is None:
            orig = tz.localize(orig)
        else:
            orig = orig.astimezone(tz)

        # === 刪除舊提醒 ===
        db.collection("notifications").document(doc.id).delete()

        # === 新提醒資料 ===
        new_data = {
            "channel_id": str(target_channel.id if target_channel else old_data.get("channel_id", interaction.channel_id)),
            "guild_id": str(interaction.guild_id),
            "datetime": orig,
            "message": message if message is not None else old_data.get("message"),
            "mention": mention if mention is not None else old_data.get("mention", "")
        }

        db.collection("notifications").add(new_data)

        await interaction.followup.send(f"✏️ 已更新提醒 / Updated reminder #{index}", ephemeral=True)

        # === 推送到監控頻道 ===
        log_channel = bot.get_channel(1356431597150408786)
        if log_channel:
            await log_channel.send(
                f"📝 **提醒被編輯**\n"
                f"👤 操作者：{interaction.user} ({interaction.user.id})\n"
                f"🌐 伺服器：{interaction.guild.name} ({interaction.guild.id})\n"
                f"📌 原提醒：{old_data['datetime'].astimezone(tz).strftime('%Y-%m-%d %H:%M')} - {old_data['message']}\n"
                f"🆕 新提醒：{new_data['datetime'].astimezone(tz).strftime('%Y-%m-%d %H:%M')} - {new_data['message']}"
            )

    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

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
                "`/edit_notify` - Edit a reminder\n"
                "`/help` - View the list of available commands"
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
                "`/edit_notify` - 編輯提醒\n"
                "`/help` - 查看指令列表"
            )
        await interaction.response.send_message(content, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(
            f"❌ 錯誤：{e}\n⚠️ 發送說明時發生錯誤 / Help command failed.", ephemeral=True)

# === 通知推播 ===
@tasks.loop(seconds=30)
async def notify_loop():
    now = datetime.now(tz).replace(second=0, microsecond=0)
    future = now + timedelta(seconds=30)

    docs = db.collection("notifications") \
        .where("datetime", ">=", now) \
        .where("datetime", "<", future) \
        .stream()

    for doc in docs:
        data = doc.to_dict()
        channel = bot.get_channel(int(data["channel_id"]))
        if channel:
            try:
                await channel.send(
                    f'{data.get("mention", "")} \n⏰ **活動提醒 / Reminder** ⏰\n{data["message"]}'
                )
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
