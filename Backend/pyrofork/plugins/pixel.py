import os
import requests
from time import time
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from Backend.helper.custom_filter import CustomFilters
import base64

load_dotenv()

PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN")
FLOOD_WAIT = 30
last_command_time = {}

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_stats(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    if user_id in last_command_time and now - last_command_time[user_id] < FLOOD_WAIT:
        await message.reply_text(f"Lütfen {FLOOD_WAIT} saniye bekleyin.")
        return
    last_command_time[user_id] = now

    if not PIXELDRAIN_API_KEY:
        await message.reply_text("PIXELDRAIN API key bulunamadı (.env).")
        return

    try:
        basic_auth = base64.b64encode(f":{PIXELDRAIN_API_KEY}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic_auth}",
            "User-Agent": "PyrogramBot"
        }

        response = requests.get(
            "https://pixeldrain.com/api/user/files",
            headers=headers,
            timeout=15
        )

        if response.status_code != 200:
            await message.reply_text(
                f"API Hatası\nHTTP Kod: {response.status_code}"
            )
            return

        files = response.json()

        count = len(files) if isinstance(files, list) else "N/A"
        text = f"Toplam dosya sayısı: {count}"
        await message.reply_text(text)

    except Exception as e:
        await message.reply_text("Bir hata oluştu.")
        print("PixelDrain hata:", e)
