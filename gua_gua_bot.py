# gua_gua_bot.py
import os
import re
import json
import pytz
#import deepl
import base64
import discord
import aiohttp
import requests
import asyncio
import firebase_admin
import logging
import sys

from dotenv import load_dotenv
from discord import app_commands
from googletrans import Translator
from discord.ui import View, Button
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from firebase_admin import credentials, firestore
from aiohttp import ClientError, ClientTimeout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False  # 避免重複輸出

# === ENV ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
REDEEM_API_URL = os.getenv("REDEEM_API_URL")
redeem_submit_url = f"{REDEEM_API_URL}/redeem_submit"
retry_failed_url = f"{REDEEM_API_URL}/retry_failed"
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
        error_ids = []  # 確保初始化，避免未定義
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
            msg = f"⚠️ 無效 ID（非 9 位數字） / Invalid ID(s) (not 9 digits):`{', '.join(invalid_ids)}`"
            await interaction.followup.send(msg, ephemeral=True)
            return

        success = []
        exists = []
        for pid in valid_ids:
            ref = db.collection("ids").document(guild_id).collection("players").document(pid)
            if ref.get().exists:
                exists.append(pid)
            else:
                # 這裡直接查 nickname 並儲存
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{REDEEM_API_URL}/add_id", json={
                        "guild_id": guild_id,
                        "player_id": pid
                    }) as resp:
                        if resp.status == 200:
                            success.append(pid)
                        elif resp.status == 409:
                            exists.append(pid)
                        else:
                            error_ids.append(pid)  # 可另設一類

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
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        doc = ref.get()

        if doc.exists:
            info = doc.to_dict()
            ref.delete()
            msg = f"✅ 已移除 / Removed player_id `{player_id}`"
            await interaction.followup.send(msg, ephemeral=True)

            # === 傳送到監控頻道 ===
            log_channel = bot.get_channel(1356431597150408786)
            if log_channel:
                nickname = info.get("name", "")
                await log_channel.send(
                    f"🗑️ **ID 被移除**\n"
                    f"👤 操作者：{interaction.user} ({interaction.user.id})\n"
                    f"🌐 伺服器：{interaction.guild.name} ({interaction.guild.id})\n"
                    f"📌 移除 ID：{player_id} {f'({nickname})' if nickname else ''}"
                )
        else:
            await interaction.followup.send(f"❌ 找不到該 ID / ID not found `{player_id}`", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="list_ids", description="列出所有玩家 ID / List all player IDs")
async def list_ids(interaction: discord.Interaction):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        guild_id = str(interaction.guild_id)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{REDEEM_API_URL}/list_ids?guild_id={guild_id}") as resp:
                result = await resp.json()

        players = result.get("players", [])
        if not players:
            await interaction.response.send_message("📭 沒有任何 ID / No player ID found", ephemeral=True)
            return

        PAGE_SIZE = 20
        total_pages = (len(players) + PAGE_SIZE - 1) // PAGE_SIZE

        def format_page(page):
            start = (page - 1) * PAGE_SIZE
            end = start + PAGE_SIZE
            page_players = players[start:end]
            lines = [
                f"- `{p.get('id', '未知ID')}` ({p.get('name')})" if p.get("name") else f"- `{p.get('id', '未知ID')}`"
                for p in page_players
            ]
            return f"📋 玩家清單（第 {page}/{total_pages} 頁）\n" + "\n".join(lines)

        class PageView(View):
            def __init__(self):
                super().__init__(timeout=60)
                self.page = 1

            def update_buttons(self):
                for item in self.children:
                    if isinstance(item, Button):
                        if item.label == "⬅️ 上一頁":
                            item.disabled = self.page == 1
                        elif item.label == "➡️ 下一頁":
                            item.disabled = self.page >= total_pages

            async def update_message(self, interaction):
                self.update_buttons()
                content = format_page(self.page)
                await interaction.response.edit_message(content=content, view=self)

            @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.gray)
            async def prev_button(self, interaction: discord.Interaction, button: Button):
                self.page -= 1
                await self.update_message(interaction)

            @discord.ui.button(label="➡️ 下一頁", style=discord.ButtonStyle.gray)
            async def next_button(self, interaction: discord.Interaction, button: Button):
                self.page += 1
                await self.update_message(interaction)

        view = PageView()
        await interaction.followup.send(content=format_page(1), view=view, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

# === Redeem 兌換 ===
@tree.command(name="redeem_submit", description="提交兌換碼 / Submit redeem code")
@app_commands.describe(code="要兌換的禮包碼", player_id="選填：指定兌換的玩家 ID（單人兌換）")
async def redeem_submit(interaction: discord.Interaction, code: str, player_id: str = None):
    await interaction.response.send_message("🎁 兌換已開始處理 / Redemption started. 系統稍後會回報結果 / Result will be reported shortly.", ephemeral=True)
    if player_id:
        asyncio.create_task(trigger_backend_redeem(interaction, code, [player_id]))
    else:
        asyncio.create_task(trigger_backend_redeem(interaction, code))


async def get_player_ids(guild_id):
    docs = db.collection("ids").document(guild_id).collection("players").stream()
    return [doc.id for doc in docs]


async def trigger_backend_redeem(interaction: discord.Interaction, code: str, player_ids: list = None):
    guild_id = str(interaction.guild_id)

    if player_ids is None:
        player_ids = await get_player_ids(guild_id)

    if not player_ids:
        await interaction.followup.send("⚠️ 沒有找到任何玩家 ID / No player ID found", ephemeral=True)
        return

    try:
        payload = {
            "code": code,
            "player_ids": player_ids,
            "debug": False
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(redeem_submit_url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        logger.info(f"[{guild_id}] ✅ 成功觸發後端兌換流程（未等待完成）")
                    else:
                        logger.error(f"[{guild_id}] ❌ API 回傳錯誤狀態：{resp.status}")
            except (asyncio.TimeoutError, ClientError) as e:
                logger.warning(f"[{guild_id}] 發送請求超時 / Request timeout. 將由 webhook 回報：{e}")
                # ❌ 移除這行，以免 Discord 顯示錯誤訊息：
                # await interaction.followup.send(f"❌ 發送請求失敗 / Failed to send request. 錯誤信息 / Error:{str(e)}", ephemeral=True)
    except Exception as e:
        logger.exception(f"[Critical Error] trigger_backend_redeem 發生錯誤（guild_id: {guild_id}）")

@tree.command(name="retry_failed", description="重新兌換失敗的 ID / Retry failed ID")
@app_commands.describe(code="禮包碼 / Redeem code")
async def retry_failed(interaction: discord.Interaction, code: str):
    await interaction.response.send_message("🎁 重新兌換開始 / Retrying redemption. 系統稍後會回報結果 / System will report back shortly.", ephemeral=True)
    
    # 從 Firestore 找到失敗的 ID
    failed_docs = db.collection("failed_redeems").document(code).collection("players").stream()
    player_ids = [doc.id for doc in failed_docs]

    if not player_ids:
        await interaction.followup.send("⚠️ 沒有找到失敗的 ID / No failed IDs found", ephemeral=True)
        return

    # 呼叫現有的兌換流程
    try:
        payload = {
            "code": code,
            "player_ids": player_ids,
            "debug": False
        }
        # 呼叫後端 API（這裡直接進行兌換）
        async with aiohttp.ClientSession() as session:
            async with session.post(retry_failed_url, json=payload) as resp:
                if resp.status == 200:
                    await interaction.followup.send(f"🎁 重新兌換 {len(player_ids)} 個失敗的 ID 已發送到後端進行處理", ephemeral=True)
                else:
                    # 處理 API 錯誤回應
                    error_message = await resp.text()
                    await interaction.followup.send(f"❌ 發生錯誤 / Error:{error_message}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤 / Error:{e}", ephemeral=True)

# === 活動提醒 ===
@tree.command(name="add_notify", description="新增提醒 / Add reminder")
@app_commands.describe(
    date="YYYY-MM-DD, 可輸入多個 / Multiple allowed",
    time="HH:MM, 可輸入多個 / Multiple allowed",
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
        await interaction.followup.send(f"❌ 錯誤：{e} / Error: {e}", ephemeral=True)

@tree.command(name="list_notify", description="查看提醒列表 / View reminder list")
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
            mention = data.get("mention", "")
            channel_id = data.get("channel_id", "")
            channel = bot.get_channel(int(channel_id))
            channel_name = f"<#{channel_id}>" if channel else f"未知頻道 ({channel_id})"
            rows.append(f"{i+1}. {time_str} - {data.get('message')} {mention} → {channel_name}")

        await interaction.response.send_message("\n".join(rows) if rows else "📭 沒有提醒資料 / No reminders found", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="remove_notify", description="移除提醒 / Remove reminder")
@app_commands.describe(index="提醒編號 / Reminder index")
async def remove_notify(interaction: discord.Interaction, index: int):
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
        data = doc.to_dict()
        db.collection("notifications").document(doc.id).delete()
        await interaction.followup.send(f"🗑️ 已刪除 / Removed reminder #{index}: {data['message']}", ephemeral=True)

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
        await interaction.followup.send(f"❌ 錯誤：{e}", ephemeral=True)

@tree.command(name="edit_notify", description="編輯提醒 / Edit reminder")
@app_commands.describe(
    index="提醒編號 / Reminder index",
    date="新日期 YYYY-MM-DD / New date",
    time="新時間 HH:MM / New time",
    message="新訊息 / New message",
    mention="新標記 / New mention",
    target_channel="提醒要送出的頻道 / Target channel to send the reminder"
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
            await interaction.followup.send("❌ 時間格式錯誤，無法修改 / Invalid original time format, cannot edit.", ephemeral=True)
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
@app_commands.describe(lang="選擇語言 / Please choose a language")
@app_commands.choices(lang=LANG_CHOICES)
async def help_command(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    try:
        if lang.value == "en":
            content = (
                "**GuaGuaBOT Help (English)**\n"
                "`/add_id` - Add one or more player IDs (comma-separated)\n"
                "`/remove_id` - Remove a player ID\n"
                "`/list_ids` - List all saved player IDs\n"
                "`/redeem_submit` - Submit a redeem code\n"
                "`/retry_failed` - Retry failed ID redemptions\n"
                "`/update_names` - Refresh and update all player ID names\n"
                "`/add_notify` - Add reminders (supports multiple dates and times)\n"
                "`/list_notify` - View reminder list\n"
                "`/remove_notify` - Remove a reminder\n"
                "`/edit_notify` - Edit a reminder\n"
                "`/help` - View the list of available commands\n"
                "`Translation` - Mention the bot and reply to a message to auto-translate, or use the right-click menu 'Translate Message'"
            )
        else:
            content = (
                "**呱呱BOT 指令說明（繁體中文）**\n"
                "`/add_id` - 新增一個或多個玩家 ID（用逗號分隔）\n"
                "`/remove_id` - 移除玩家 ID\n"
                "`/list_ids` - 顯示所有已儲存的 ID\n"
                "`/redeem_submit` - 提交兌換碼\n"
                "`/retry_failed` - 重新兌換失敗的 ID\n"
                "`/update_names` - 重新查詢並更新所有 ID 的角色名稱\n"
                "`/add_notify` - 新增提醒（支援多個日期與時間）\n"
                "`/list_notify` - 查看提醒列表\n"
                "`/remove_notify` - 移除提醒\n"
                "`/edit_notify` - 編輯提醒\n"
                "`/help` - 查看指令列表\n"
                "`翻譯功能` - 標記機器人並回覆訊息即可自動翻譯中英文，或使用右鍵選單「翻譯此訊息」"
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
                logger.info(f"[Error] 發送提醒失敗: {e}")
        db.collection("notifications").document(doc.id).delete()

# === 上線後同步 ===
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync()
        logger.info(f"✅ Synced {len(synced)} global commands: {[c.name for c in synced]}")
    except Exception as e:
        logger.info(f"❌ Failed to sync commands: {e}")
    if not notify_loop.is_running():
        notify_loop.start()

translator = Translator()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions and message.reference:
        try:
            original_msg = await message.channel.fetch_message(message.reference.message_id)
            text = original_msg.content.strip()
            if not text:
                return

            detected = translator.detect(text).lang.lower()

            if detected == "th":
                target_langs = [("en", "English"), ("zh-tw", "繁體中文")]
            elif detected in ["zh-cn", "zh-tw", "zh"]:
                target_langs = [("en", "English")]
            else:
                target_langs = [("zh-tw", "繁體中文")]

            embeds = []
            for lang_code, lang_label in target_langs:
                result = translator.translate(text, dest=lang_code)
                embed = discord.Embed(
                    title=f"🌐 翻譯完成 / Translation Result ({lang_label})",
                    color=discord.Color.blue()
                )
                embed.add_field(name="📤 原文 / Original", value=text[:1024], inline=False)
                embed.add_field(name="📥 翻譯 / Translated", value=result.text[:1024], inline=False)
                embed.set_footer(text=f"語言偵測 / Detected: {detected} → {lang_label}")
                embeds.append(embed)

            for embed in embeds:
                await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ 翻譯失敗：{e}")

    await bot.process_commands(message)

@tree.context_menu(name="翻譯此訊息 / Translate Message")
async def context_translate(interaction: discord.Interaction, message: discord.Message):
    try:
        await interaction.response.defer(ephemeral=True)

        text = message.content.strip()
        if not text:
            await interaction.followup.send("⚠️ 原文為空 / The original message is empty.", ephemeral=True)
            return

        target_lang = "en" if any(u'\u4e00' <= ch <= u'\u9fff' for ch in text) else "zh-tw"
        result = translator.translate(text, dest=target_lang)

        embed = discord.Embed(
            title="🌐 翻譯完成 / Translation Result",
            color=discord.Color.green()
        )
        embed.add_field(name="📤 原文 / Original", value=text[:1024], inline=False)
        embed.add_field(name="📥 翻譯 / Translated", value=result.text[:1024], inline=False)
        embed.set_footer(text=f"目標語言 / Target: {target_lang}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ 翻譯失敗：{e}", ephemeral=True)

@tree.command(name="update_names", description="重新查詢所有 ID 並更新名稱")
@app_commands.describe(guild_id="請輸入要更新的 Discord 伺服器 ID / Please enter the Discord server ID to update")
async def update_names(interaction: discord.Interaction, guild_id: str):
    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{REDEEM_API_URL}/update_names_api", json={
                "guild_id": guild_id
            }) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await interaction.followup.send(f"❌ API 回傳錯誤 / API error:{resp.status}\n{text}", ephemeral=True)
                    return

                result = await resp.json()
                updated = result.get("updated", [])

                if updated:
                    lines = [f"- {u['player_id']} ➜ {u['name']}" for u in updated]
                    summary = "\n".join(lines)
                    logger.info(f"[update_names] 共更新 {len(updated)} 筆名稱：\n{summary}")
                    await interaction.followup.send(
                        f"✨ 共更新 {len(updated)} 筆名稱 / Updated {len(updated)} names：\n\n{summary}", ephemeral=True
                    )

                else:
                    logger.info(f"[update_names] 無任何名稱需要更新 / No names to update")
                    await interaction.followup.send("✅ 沒有任何名稱需要更新 / No name updates required.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{e}", ephemeral=True)

bot.run(TOKEN)