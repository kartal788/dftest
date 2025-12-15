import os
import requests
import base64
from time import time
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from Backend.helper.custom_filter import CustomFilters

load_dotenv()

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN")

FLOOD_WAIT = 60
last_command_time = {}

API_BASE = "https://pixeldrain.com/api"

def get_headers():
    auth = base64.b64encode(f":{PIXELDRAIN_API_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "User-Agent": "PyrogramBot"
    }

def fetch_all_files():
    page = 1
    all_files = []

    while True:
        r = requests.get(
            f"{API_BASE}/user/files?page={page}",
            headers=get_headers(),
            timeout=20
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

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_stats(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    if user_id in last_command_time and now - last_command_time[user_id] < FLOOD_WAIT:
        await message.reply_text("Lütfen biraz bekleyin.")
        return
    last_command_time[user_id] = now

    if not PIXELDRAIN_API_KEY:
        await message.reply_text("PIXELDRAIN API key yok.")
        return

    await message.reply_text("Veriler toplanıyor...")

    try:
        files = fetch_all_files()

        total_files = len(files)
        total_bytes = sum(f.get("size", 0) for f in files)

        mb = total_bytes / (1024 * 1024)
        gb = total_bytes / (1024 * 1024 * 1024)

        # Basit günlük trafik tahmini (son 30 gün varsayımı)
        daily_mb = mb / 30 if mb > 0 else 0

        text = (
            "PixelDrain Gerçek İstatistikler\n\n"
            f"Toplam Dosya: {total_files}\n"
            f"Toplam Boyut: {mb:.2f} MB ({gb:.2f} GB)\n"
            f"Günlük Trafik Tahmini: {daily_mb:.2f} MB\n\n"
            "Tüm dosyaları silmek için:\n"
            "/pixeldrain_sil"
        )

        await message.reply_text(text)

    except Exception as e:
        await message.reply_text("Hata oluştu.")
        print("PixelDrain hata:", e)

@Client.on_message(filters.command("pixeldrain_sil") & filters.private & CustomFilters.owner)
async def pixeldrain_delete_all(client: Client, message: Message):
    if not PIXELDRAIN_API_KEY:
        await message.reply_text("PIXELDRAIN API key yok.")
        return

    await message.reply_text("Tüm dosyalar siliniyor...")

    try:
        files = fetch_all_files()
        deleted = 0

        for f in files:
            file_id = f.get("id")
            if not file_id:
                continue

            r = requests.delete(
                f"{API_BASE}/file/{file_id}",
                headers=get_headers(),
                timeout=15
            )

            if r.status_code == 200:
                deleted += 1

        await message.reply_text(
            f"Silme tamamlandı.\n"
            f"Silinen dosya: {deleted}"
        )

    except Exception as e:
        await message.reply_text("Silme sırasında hata oluştu.")
        print("PixelDrain silme hata:", e)
