from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from Backend.helper import metadata as md_helper
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import aiohttp

# ------------ SADECE ENV'DEN DATABASE VE PIXELDRAIN API KEY AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
PIXELDRAIN_KEY = os.getenv("PIXELDRAIN", "")

if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

MONGO_URL = db_urls[1]

# ------------ MONGO BAĞLANTISI ------------
client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    if db is not None:
        return
    db_names = await client.list_database_names()
    db = client[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ Onay Bekleyen Kullanıcıları Sakla ------------
awaiting_confirmation = {}  # user_id -> asyncio.Task

# ------------ Pixeldrain API'den Dosya Adını Al ------------
async def get_pixeldrain_filename(url: str) -> str | None:
    api_url = f"https://pixeldrain.com/api/file/info/{url.split('/')[-1]}"
    headers = {"Authorization": f"Bearer {PIXELDRAIN_KEY}"} if PIXELDRAIN_KEY else {}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(api_url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("name")
        except Exception:
            return None

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_link(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Lütfen bir Pixeldrain linki girin. Örnek: /ekle <link>")

    link = message.command[1]
    filename = await get_pixeldrain_filename(link)
    if not filename:
        return await message.reply_text("❌ Dosya adı alınamadı. Linki kontrol edin veya API key eksik.")

    await init_db()
    await message.reply_text(f"🔎 Metadata çekiliyor: {filename}")

    # TMDb/IMDb metadata çek
    meta = await md_helper.metadata(filename=filename, channel=message.chat.id, msg_id=message.id)
    if not meta:
        return await message.reply_text("❌ Metadata alınamadı.")

    # Hangi koleksiyona kaydedilecek?
    collection = movie_col if meta["media_type"] == "movie" else series_col
    await collection.insert_one(meta)
    await message.reply_text(f"✅ {meta['title']} veritabanına eklendi.")

# ------------ /sil Komutu ------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client, message):
    user_id = message.from_user.id
    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\n"
        "Onaylamak için **Evet**, iptal etmek için **Hayır** yazın.\n"
        "⏱ 60 saniye içinde cevap vermezsen işlem otomatik iptal edilir."
    )

    if user_id in awaiting_confirmation:
        awaiting_confirmation[user_id].cancel()

    async def timeout():
        await asyncio.sleep(60)
        if user_id in awaiting_confirmation:
            awaiting_confirmation.pop(user_id, None)
            await message.reply_text("⏰ Zaman doldu, silme işlemi otomatik olarak iptal edildi.")

    task = asyncio.create_task(timeout())
    awaiting_confirmation[user_id] = task

# ------------ "Evet" veya "Hayır" Mesajı ------------
@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    if text == "evet":
        await message.reply_text("🗑️ Silme işlemi başlatılıyor...")
        await init_db()

        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})

        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            f"✅ Silme işlemi tamamlandı.\n\n"
            f"📌 Filmler silindi: {movie_count}\n"
            f"📌 Diziler silindi: {series_count}"
        )

    elif text == "hayır":
        await message.reply_text("❌ Silme işlemi iptal edildi.")
