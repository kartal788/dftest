import os
import requests
from time import time
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from Backend.helper.custom_filter import CustomFilters

# .env dosyasını yükle
load_dotenv()

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN")

# Flood ayarları
FLOOD_WAIT = 30  # saniye
last_command_time = {}

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_stats(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    # Flood kontrolü
    if user_id in last_command_time and now - last_command_time[user_id] < FLOOD_WAIT:
        await message.reply_text(f"Lütfen {FLOOD_WAIT} saniye bekleyin.")
        return
    last_command_time[user_id] = now

    if not PIXELDRAIN_API_KEY:
        await message.reply_text("PIXELDRAIN API key bulunamadı (.env).")
        return

    try:
        response = requests.get(
            "https://pixeldrain.com/api/account",
            headers={
                "Authorization": f"Bearer {PIXELDRAIN_API_KEY}",
                "User-Agent": "PyrogramBot"
            },
            timeout=15
        )

        if response.status_code != 200:
            await message.reply_text(
                f"PixelDrain API hatası\n"
                f"HTTP Kod: {response.status_code}"
            )
            return

        data = response.json()

        # Güvenli string dönüşümü
        username = str(data.get("username", "Bilinmiyor"))
        file_count = str(data.get("file_count", "N/A"))
        storage_used = str(data.get("storage_used", "N/A"))
        bandwidth_used = str(data.get("bandwidth_used", "N/A"))
        plan = str(data.get("plan", "N/A"))

        text = (
            "PixelDrain İstatistikleri\n\n"
            f"Kullanıcı: {username}\n"
            f"Dosya Sayısı: {file_count}\n"
            f"Depolama: {storage_used}\n"
            f"Trafik: {bandwidth_used}\n"
            f"Plan: {plan}"
        )

        await message.reply_text(text)

    except Exception as e:
        await message.reply_text("Bir hata oluştu.")
        print("PixelDrain hata:", e)
