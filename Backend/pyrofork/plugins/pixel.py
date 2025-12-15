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
delete_waiting = {}  # user_id: timestamp


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


# ---------------- FETCH FILES (DEDUP FIX) ----------------

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


# ---------------- PIXELDRAIN COMMAND ----------------

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
    status = await safe_reply(message, "📂 Dosyalar alınıyor...")

    # 🗑️ SİLME ONAYI
    if args and args[0].lower() == "sil":
        delete_waiting[user_id] = time()
        await safe_edit(
            status,
            "⚠️ **TÜM PixelDrain dosyaları silinecek!**\n\n"
            "Devam etmek için **EVET** yaz\n"
            "İptal için **HAYIR** yaz\n\n"
            "⏱️ 60 saniye içinde cevap verilmezse iptal edilir."
        )
        return

    # 📊 DOSYA LİSTESİ + ÖZET
    try:
        files = await asyncio.to_thread(fetch_all_files_safe)
        total_bytes = sum(f.get("size", 0) for f in files)

        file_names = []
        for f in files:
            name = f.get("name") or "isimsiz_dosya"
            file_names.append(name)

        # 🔹 10 ve altı → mesaj
        if len(file_names) <= 10:
            file_list_text = "\n".join(f"• {n}" for n in file_names)
            await safe_edit(
                status,
                "📊 **PixelDrain Özet**\n\n"
                f"Toplam Dosya: {len(files)}\n"
                f"Toplam Boyut: {human_size(total_bytes)}\n\n"
                "**📁 Dosyalar:**\n"
                f"{file_list_text}\n\n"
                "🗑️ Tüm dosyaları silmek için:\n"
                "`/pixeldrain sil`"
            )

        # 🔹 10’dan fazla → TXT
        else:
            txt_content = "\n".join(file_names)
            txt_path = "dosyalar.txt"

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(txt_content)

            await client.send_document(
                chat_id=message.chat.id,
                document=txt_path,
                caption=(
                    "📊 **PixelDrain Özet**\n\n"
                    f"Toplam Dosya: {len(files)}\n"
                    f"Toplam Boyut: {human_size(total_bytes)}\n\n"
                    "📁 Dosya listesi ektedir.\n\n"
                    "🗑️ Tüm dosyaları silmek için:\n"
                    "`/pixeldrain sil`"
                )
            await status.delete()
            os.remove(txt_path)

    except Exception as e:
        await safe_edit(status, "❌ Hata oluştu.")
        print("PixelDrain hata:", e)


# ---------------- EVET / HAYIR CONFIRM ----------------

@Client.on_message(
    filters.private
    & CustomFilters.owner
    & filters.text
    & ~filters.regex(r"^/")
)
async def pixeldrain_confirm_message(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip().lower()  # 🔥 case-insensitive

    if user_id not in delete_waiting:
        return

    if time() - delete_waiting[user_id] > 60:
        delete_waiting.pop(user_id, None)
        await safe_reply(message, "⏱️ Süre doldu. Silme iptal edildi.")
        return

    if text == "hayır":
        delete_waiting.pop(user_id, None)
        await safe_reply(message, "❌ Silme iptal edildi.")
        return

    if text == "evet":
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
