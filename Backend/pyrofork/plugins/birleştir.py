from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import PTN
from Backend.helper.encrypt import encode_string
from Backend.logger import LOGGER
from themoviedb import aioTMDb

# ------------ ENV'DEN AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")
MONGO_URL = db_urls[1]
TMDB_API = os.getenv("TMDB_API", "")

# ------------ MONGO BAĞLANTISI ------------
client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    db_names = await client.list_database_names()
    db = client[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")
API_SEMAPHORE = asyncio.Semaphore(12)

# ------------ Onay Bekleyen Kullanıcıları Sakla ------------
awaiting_confirmation = {}  # user_id -> asyncio.Task

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client, message):
    await init_db()
    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = message.command[1]
    filename = " ".join(message.command[2:])

    # PTN ile ayrıştır
    try:
        parsed = PTN.parse(filename)
    except Exception as e:
        await message.reply_text(f"Dosya adı ayrıştırılamadı: {e}")
        return

    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    year = parsed.get("year")
    quality = parsed.get("resolution")

    if not title:
        await message.reply_text("Başlık bulunamadı, lütfen doğru bir dosya adı girin.")
        return

    # Metadata encode
    data = {"chat_id": message.chat.id, "msg_id": message.id}
    try:
        encoded_string = await encode_string(data)
    except Exception:
        encoded_string = None

    # TMDb search
    async with API_SEMAPHORE:
        if season and episode:
            tmdb_search = await tmdb.search().tv(query=title)
        else:
            tmdb_search = await tmdb.search().movies(query=title, year=year)

    if not tmdb_search:
        await message.reply_text(f"{title} için TMDb sonucu bulunamadı.")
        return

    metadata = tmdb_search[0]  # ilk sonucu alıyoruz

    # MongoDB kaydı
    record = {
        "title": title,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "url": url,
        "tmdb_id": getattr(metadata, "id", None),
        "description": getattr(metadata, "overview", ""),
        "encoded_string": encoded_string
    }

    collection = series_col if season else movie_col
    await collection.insert_one(record)
    await message.reply_text(f"✅ {title} başarıyla eklendi.")

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

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    await init_db()
    if text == "evet":
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
