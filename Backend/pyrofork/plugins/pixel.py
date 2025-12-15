import os
import requests
import base64
import asyncio
from time import time

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from dotenv import load_dotenv
from Backend.helper.custom_filter import CustomFilters

load_dotenv()

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN")
API_BASE = "https://pixeldrain.com/api"
CMD_FLOOD_WAIT = 5

last_command_time = {}


# ---------------- UTIL ----------------

def get_headers():
    auth = base64.b64encode(f":{PIXELDRAIN_API_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "User-Agent": "PyrogramBot"
    }

def human_size(size):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024


# ---------------- FETCH FILES (DEDUP SAFE) ----------------

def fetch_all_files_safe(max_pages=100):
    page = 1
    files_by_id = {}

    while page <= max_pages:
        r = requests.get(
            f"{API_BASE}/user/files?page={page}",
            headers=get_headers(),
            timeout=15
        )

        if r.status_code != 200:
            break

        files = r.json().get("files", [])
        if not files:
            break

        for f in files:
            file_id = f.get("id")
            if file_id:
                files_by_id[file_id] = f

        page += 1

    return list(files_by_id.values())


# ---------------- SAFE TELEGRAM ----------------

async def safe_reply(message: Message, text: str):
    try:
        return await message.reply_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await message.reply_text(text)

async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await msg.edit_text(text)


# ---------------- /PIXELDRAIN ----------------

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_list(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    if user_id in last_command_time and now - last_command_time[user_id] < CMD_FLOOD_WAIT:
        await safe_reply(message, "⏳ Lütfen biraz bekleyin.")
        return
    last_command_time[user_id] = now

    status = await safe_reply(message, "📂 Dosyalar alınıyor...")

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total_bytes = sum(f.get("size", 0) for f in files)
        names = [f.get("name") or "isimsiz_dosya" for f in files]

        if len(names) <= 10:
            await safe_edit(
                status,
                "📊 **PixelDrain Özet**\n\n"
                f"Toplam Dosya: {len(files)}\n"
                f"Toplam Boyut: {human_size(total_bytes)}\n\n"
                "**📁 Dosyalar:**\n" +
                "\n".join(f"• {n}" for n in names)
            )
        else:
            path = "dosyalar.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(names))

            await client.send_document(
                message.chat.id,
                path,
                caption=(
                    "📊 **PixelDrain Özet**\n\n"
                    f"Toplam Dosya: {len(files)}\n"
                    f"Toplam Boyut: {human_size(total_bytes)}"
                )
            )
            await status.delete()
            os.remove(path)

    except Exception as e:
        await safe_edit(status, "❌ Hata oluştu.")
        print("PixelDrain list error:", e)


# ---------------- /PIXELDRAINSIL ----------------

@Client.on_message(filters.command("pixeldrainsil") & filters.private & CustomFilters.owner)
async def pixeldrain_delete_all(client: Client, message: Message):
    status = await safe_reply(message, "🗑️ PixelDrain dosyaları alınıyor...")

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)

        if not files:
            await safe_edit(status, "ℹ️ Silinecek dosya yok.")
            return

        await safe_edit(
            status,
            f"🗑️ **Silme başlatıldı**\n\n"
            f"Silinecek dosya: {len(files)}"
        )

        deleted = 0
        for f in files:
            file_id = f.get("id")
            if not file_id:
                continue

            await asyncio.to_thread(
                requests.delete,
                f"{API_BASE}/file/{file_id}",
                headers=get_headers(),
                timeout=10
            )

            deleted += 1
            await asyncio.sleep(0.3)

        await safe_edit(
            status,
            f"✅ Silme tamamlandı.\nSilinen dosya: {deleted}"
        )

    except Exception as e:
        await safe_edit(status, "❌ Silme sırasında hata oluştu.")
        print("PixelDrain delete error:", e)
