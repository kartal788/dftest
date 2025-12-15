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


# ===================== UTIL =====================

def format_duration(seconds: int):
    if seconds < 0:
        return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"

def progress_bar(done, total, length=20):
    if total == 0:
        return "[--------------------] 0%"
    percent = int((done / total) * 100)
    filled = int(length * done / total)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent}%"

async def auto_update_status(msg, get_text_func, stop_event):
    while not stop_event.is_set():
        try:
            await safe_edit(msg, get_text_func())
        except Exception:
            pass
        await asyncio.sleep(15)

# ===================== /PIXELDRAINSIL =====================

@Client.on_message(filters.command("pixeldrainsil") & filters.private & CustomFilters.owner)
async def pixeldrain_delete_all(client: Client, message: Message):
    status = await safe_reply(message, "🗑️ PixelDrain dosyaları hazırlanıyor...")

    stop_event = asyncio.Event()
    start_time = time()

    deleted = 0
    total = 0
    speed = 0.0
    last_files = []

    def status_text():
        elapsed = int(time() - start_time)
        speed_calc = deleted / elapsed if elapsed > 0 else 0
        eta = int((total - deleted) / speed_calc) if speed_calc > 0 else -1

        return (
            "🗑️ **PixelDrain Silme Durumu**\n\n"
            "```\n"
            f"Geçen Süre : {format_duration(elapsed)}\n"
            f"İlerleme   : {progress_bar(deleted, total)}\n"
            f"Hız        : {speed_calc:.2f} dosya/sn\n"
            f"ETA        : {format_duration(eta)}\n"
            f"Silinen    : {deleted}/{total}\n\n"
            f"Son Dosyalar:\n" +
            "\n".join(f"- {n}" for n in last_files[-5:]) +
            "\n```"
        )

    updater = asyncio.create_task(
        auto_update_status(status, status_text, stop_event)
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
            file_id = f.get("id")
            name = f.get("name", "isimsiz")

            if not file_id:
                continue

            await asyncio.to_thread(
                requests.delete,
                f"{API_BASE}/file/{file_id}",
                headers=get_headers(),
                timeout=10
            )

            deleted += 1
            last_files.append(name)
            await asyncio.sleep(0.3)

        stop_event.set()
        updater.cancel()

        await safe_edit(
            status,
            "✅ **Silme Tamamlandı**\n\n"
            "```\n"
            f"Toplam Süre : {format_duration(int(time() - start_time))}\n"
            f"Silinen    : {deleted}\n"
            "```"
        )

    except Exception as e:
        stop_event.set()
        updater.cancel()
        await safe_edit(status, "❌ Silme sırasında hata oluştu.")
        print("PixelDrain delete error:", e)

# ===================== /PIXELDRAIN =====================

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_list(client: Client, message: Message):
    status = await safe_reply(message, "📂 Dosyalar hazırlanıyor...")

    stop_event = asyncio.Event()
    start_time = time()

    files = []
    total_bytes = 0

    def status_text():
        elapsed = int(time() - start_time)
        speed = len(files) / elapsed if elapsed > 0 else 0

        return (
            "📂 **PixelDrain Listeleme**\n\n"
            "```\n"
            f"Geçen Süre : {format_duration(elapsed)}\n"
            f"Dosya      : {len(files)}\n"
            f"Hız        : {speed:.2f} dosya/sn\n"
            f"Toplam Boyut: {human_size(total_bytes)}\n"
            "```"
        )

    updater = asyncio.create_task(
        auto_update_status(status, status_text, stop_event)
    )

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total_bytes = sum(f.get("size", 0) for f in files)

        stop_event.set()
        updater.cancel()

        names = [f.get("name") or "isimsiz" for f in files]

        if len(names) <= 10:
            await safe_edit(
                status,
                "📊 **PixelDrain Özet**\n\n"
                "```\n"
                f"Toplam Dosya : {len(files)}\n"
                f"Toplam Boyut : {human_size(total_bytes)}\n"
                "```\n\n" +
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
                    f"Toplam Dosya : {len(files)}\n"
                    f"Toplam Boyut : {human_size(total_bytes)}"
                )
            )
            await status.delete()
            os.remove(path)

    except Exception as e:
        stop_event.set()
        updater.cancel()
        await safe_edit(status, "❌ Listeleme sırasında hata oluştu.")
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
