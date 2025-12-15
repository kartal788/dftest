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
delete_waiting = {}  # user_id: timestamp

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

        files = r.json().get("files", [])
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

# ---------------- PIXELDRAIN KOMUT ----------------

@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_handler(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    if user_id in last_command_time and now - last_command_time[user_id] < CMD_FLOOD_WAIT:
        await safe_reply(message, "⏳ Lütfen biraz bekleyin.")
        return
    last_command_time[user_id] = now

    if not PIXELDRAIN_API_KEY:
        await safe_reply(message, "❌ PIXELDRAIN API key yok.")
        return

    args = message.command[1:]
    status = await safe_reply(message, "İşlem başlatıldı...")

    # 🔥 /pixeldrain sil → ONAY İSTE
    if args and args[0].lower() == "sil":
        delete_waiting[user_id] = time()

        await safe_edit(
            status,
            "⚠️ **TÜM PixelDrain dosyaları silinecek!**\n\n"
            "Devam etmek için **EVET** yaz\n"
            "İptal etmek için **HAYIR** yaz\n\n"
            "⏱️ 60 saniye içinde cevap verilmezse iptal edilir."
        )
        return

    # 📊 Özet
    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total_bytes = sum(f.get("size", 0) for f in files)

        await safe_edit(
            status,
            "📊 **PixelDrain Özet**\n\n"
            f"Toplam Dosya: {len(files)}\n"
            f"Toplam Boyut: {human_size(total_bytes)}\n\n"
            "🗑️ Tüm dosyaları silmek için:\n"
            "`/pixeldrain sil`"
        )

    except Exception as e:
        await safe_edit(status, "❌ Hata oluştu.")
        print("PixelDrain hata:", e)

# ---------------- EVET / HAYIR CEVAPLARI ----------------

@Client.on_message(
    filters.private
    & CustomFilters.owner
    & filters.text
    & ~filters.regex(r"^/")
)
async def pixeldrain_confirm_message(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip().upper()

    if user_id not in delete_waiting:
        return

    # ⏱️ Süre doldu mu?
    if time() - delete_waiting[user_id] > 60:
        delete_waiting.pop(user_id, None)
        await safe_reply(message, "⏱️ Süre doldu. Silme iptal edildi.")
        return

    # ❌ HAYIR
    if text == "HAYIR":
        delete_waiting.pop(user_id, None)
        await safe_reply(message, "❌ Silme işlemi iptal edildi.")
        return

    # ✅ EVET
    if text == "EVET":
        delete_waiting.pop(user_id, None)
        status = await safe_reply(message, "🗑️ Dosyalar siliniyor...")

        try:
            files = await asyncio.to_thread(fetch_all_files_safe)
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
            print("PixelDrain silme hata:", e)
