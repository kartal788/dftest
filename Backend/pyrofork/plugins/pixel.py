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
CMD_FLOOD_WAIT = 60
last_command_time = {}

def get_headers():
    auth = base64.b64encode(f":{PIXELDRAIN_API_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "User-Agent": "PyrogramBot"
    }

def fetch_all_files_safe(max_pages=100):
    page = 1
    all_files = []

    while page <= max_pages:
        r = requests.get(
            f"{API_BASE}/user/files?page={page}",
            headers=get_headers(),
            timeout=15
        )

        if r.status_code != 200:
            break

        data = r.json()
        files = data.get("files", [])

        if not files:
            break

        all_files.extend(files)
        page += 1

    return all_files

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

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_stats(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    # Komut flood koruması
    if user_id in last_command_time and now - last_command_time[user_id] < CMD_FLOOD_WAIT:
        await safe_reply(message, "Lütfen biraz bekleyin.")
        return
    last_command_time[user_id] = now

    if not PIXELDRAIN_API_KEY:
        await safe_reply(message, "PIXELDRAIN API key yok.")
        return

    status = await safe_reply(message, "Veriler toplanıyor...")

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)

        total_files = len(files)
        total_bytes = sum(f.get("size", 0) for f in files)

        mb = total_bytes / (1024 * 1024)
        gb = total_bytes / (1024 * 1024 * 1024)
        daily_mb = mb / 30 if mb else 0

        text = (
            "PixelDrain Gerçek İstatistikler\n\n"
            f"Toplam Dosya: {total_files}\n"
            f"Toplam Boyut: {mb:.2f} MB ({gb:.2f} GB)\n"
            f"Günlük Trafik Tahmini: {daily_mb:.2f} MB\n\n"
            "Tüm dosyaları silmek için:\n"
            "/pixeldrain_sil"
        )

        await safe_edit(status, text)

    except Exception as e:
        await safe_edit(status, "Hata oluştu.")
        print("PixelDrain hata:", e)

@Client.on_message(filters.command("pixeldrain_sil") & filters.private & CustomFilters.owner)
async def pixeldrain_delete_all(client: Client, message: Message):
    if not PIXELDRAIN_API_KEY:
        await safe_reply(message, "PIXELDRAIN API key yok.")
        return

    status = await safe_reply(message, "Tüm dosyalar siliniyor...")

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        deleted = 0

        for f in files:
            file_id = f.get("id")
            if not file_id:
                continue

            r = requests.delete(
                f"{API_BASE}/file/{file_id}",
                headers=get_headers(),
                timeout=10
            )

            if r.status_code == 200:
                deleted += 1

            await asyncio.sleep(0.3)  # PixelDrain + Telegram rate limit

        await safe_edit(
            status,
            f"Silme tamamlandı.\nSilinen dosya: {deleted}"
        )

    except Exception as e:
        await safe_edit(status, "Silme sırasında hata oluştu.")
        print("PixelDrain silme hata:", e)
