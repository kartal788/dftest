import os
import base64
import requests
import asyncio
from time import time

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from dotenv import load_dotenv
from Backend.helper.custom_filter import CustomFilters

# ===================== CONFIG =====================

load_dotenv()

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN")
API_BASE = "https://pixeldrain.com/api"
UPDATE_INTERVAL = 15

# ===================== SAFE TELEGRAM =====================

async def safe_reply(message: Message, text: str):
    try:
        return await message.reply_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await message.reply_text(text)

async def safe_edit(message: Message, text: str):
    try:
        return await message.edit_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await message.edit_text(text)

# ===================== UTIL =====================

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

def format_duration(seconds: int):
    if seconds < 0:
        return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"

def progress_bar(done, total, length=20):
    if total == 0:
        return "[░░░░░░░░░░░░░░░░░░░░] 0%"
    percent = int((done / total) * 100)
    filled = int(length * done / total)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent}%"

async def auto_update_status(msg, get_text, stop_event):
    while not stop_event.is_set():
        try:
            await safe_edit(msg, get_text())
        except Exception:
            pass
        await asyncio.sleep(UPDATE_INTERVAL)

def emoji_summary(total_files, total_size, elapsed, speed):
    return (
        "📊 **PixelDrain Özeti**\n\n"
        f"📁 Dosya Sayısı : {total_files}\n"
        f"💾 Toplam Boyut : {human_size(total_size)}\n"
        f"⏱️ Geçen Süre  : {format_duration(elapsed)}\n"
        f"🚀 Ortalama Hız: {speed:.2f} dosya/sn"
    )

# ===================== PIXELDRAIN API =====================

def fetch_all_files_safe(max_pages=100):
    page = 1
    files = {}

    while page <= max_pages:
        r = requests.get(
            f"{API_BASE}/user/files?page={page}",
            headers=get_headers(),
            timeout=15
        )
        if r.status_code != 200:
            break

        data = r.json().get("files", [])
        if not data:
            break

        for f in data:
            if f.get("id"):
                files[f["id"]] = f

        page += 1

    return list(files.values())

# ===================== /PIXELDRAINSIL =====================

@Client.on_message(filters.command("pixeldrainsil") & filters.private & CustomFilters.owner)
async def pixeldrain_delete_all(client: Client, message: Message):
    status = await safe_reply(message, "🗑️ PixelDrain silme başlatılıyor...")

    stop_event = asyncio.Event()
    start_time = time()

    deleted = 0
    total = 0
    last_files = []

    def progress_text():
        elapsed = int(time() - start_time)
        speed = deleted / elapsed if elapsed > 0 else 0
        eta = int((total - deleted) / speed) if speed > 0 else -1

        return (
            "🔄 **PixelDrain İşlem Durumu**\n\n"
            f"⏱️ Geçen Süre  : {format_duration(elapsed)}\n"
            f"📊 İlerleme    : {progress_bar(deleted, total)}\n"
            f"📁 İşlenen     : {deleted} / {total}\n\n"
            f"🚀 Hız         : {speed:.2f} dosya/sn\n"
            f"⏳ Kalan Süre  : {format_duration(eta)}\n\n"
            "📄 Son Dosyalar:\n" +
            "\n".join(f"• {n}" for n in last_files[-5:]) +
            ("\n• (henüz yok)" if not last_files else "")
        )

    updater = asyncio.create_task(
        auto_update_status(status, progress_text, stop_event)
    )

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total = len(files)

        if total == 0:
            stop_event.set()
            updater.cancel()
            await safe_edit(status, "ℹ️ Silinecek dosya yok.")
            return

        for f in files:
            await asyncio.to_thread(
                requests.delete,
                f"{API_BASE}/file/{f['id']}",
                headers=get_headers(),
                timeout=10
            )
            deleted += 1
            last_files.append(f.get("name", "isimsiz"))
            await asyncio.sleep(0.3)

        stop_event.set()
        updater.cancel()

        elapsed = int(time() - start_time)
        speed = deleted / elapsed if elapsed > 0 else 0

        await safe_edit(
            status,
            emoji_summary(deleted, 0, elapsed, speed)
        )

    except Exception as e:
        stop_event.set()
        updater.cancel()
        await safe_edit(status, "❌ Silme sırasında hata oluştu.")
        print("PixelDrain delete error:", e)

# ===================== /PIXELDRAIN =====================

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_list(client: Client, message: Message):
    status = await safe_reply(message, "📂 PixelDrain dosyaları alınıyor...")

    stop_event = asyncio.Event()
    start_time = time()
    files = []

    def progress_text():
        elapsed = int(time() - start_time)
        speed = len(files) / elapsed if elapsed > 0 else 0
        size = sum(f.get("size", 0) for f in files)

        return (
            "🔄 **PixelDrain Listeleme**\n\n"
            f"⏱️ Geçen Süre  : {format_duration(elapsed)}\n"
            f"📁 Dosya      : {len(files)}\n"
            f"💾 Boyut      : {human_size(size)}\n"
            f"🚀 Hız        : {speed:.2f} dosya/sn"
        )

    updater = asyncio.create_task(
        auto_update_status(status, progress_text, stop_event)
    )

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total_bytes = sum(f.get("size", 0) for f in files)

        stop_event.set()
        updater.cancel()

        elapsed = int(time() - start_time)
        speed = len(files) / elapsed if elapsed > 0 else 0

        await safe_edit(
            status,
            emoji_summary(len(files), total_bytes, elapsed, speed)
        )

    except Exception as e:
        stop_event.set()
        updater.cancel()
        await safe_edit(status, "❌ Listeleme sırasında hata oluştu.")
        print("PixelDrain list error:", e)
