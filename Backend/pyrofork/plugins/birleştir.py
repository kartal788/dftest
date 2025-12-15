from pyrogram import Client, filters
from pyrogram.types import Message
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import os
import asyncio
import json
import PTN
from time import time
from Backend.helper.encrypt import encode_string
from Backend.logger import LOGGER
from themoviedb import aioTMDb
import tempfile
import traceback

# ----------------- ENV -----------------
DATABASE_URLS = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_URLS.split(",") if u.strip()]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")
MONGO_URL = db_urls[1]

TMDB_API = os.getenv("TMDB_API", "")

# ----------------- Mongo Async -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    db_names = await client.list_database_names()
    if len(db_names) < 2:
        raise Exception("İkinci database bulunamadı!")
    db = client[db_names[1]]  # ikinci database
    movie_col = db["movie"]
    series_col = db["tv"]

# ----------------- TMDb -----------------
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")
API_SEMAPHORE = asyncio.Semaphore(12)

# ----------------- Onay Bekleyen -----------------
awaiting_confirmation = {}  # user_id -> asyncio.Task
flood_wait = 30
last_command_time = {}  # kullanıcı_id : zaman

# ----------------- /ekle Komutu -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client: Client, message: Message):
    await init_db()
    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = message.command[1]
    filename = " ".join(message.command[2:])

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

    metadata = tmdb_search[0]

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

# ----------------- /sil Komutu -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client: Client, message: Message):
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
async def handle_confirmation(client: Client, message: Message):
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

# ----------------- /vindir Komutu -----------------
def export_collections_to_json(url):
    client = MongoClient(url)
    db_names = client.list_database_names()
    if len(db_names) < 2:
        return None
    db = client[db_names[1]]  # ikinci database
    movie_data = list(db["movie"].find({}, {"_id": 0}))
    tv_data = list(db["tv"].find({}, {"_id": 0}))
    return {"movie": movie_data, "tv": tv_data}

@Client.on_message(filters.command("vindir") & filters.private & CustomFilters.owner)
async def download_collections(client: Client, message: Message):
    user_id = message.from_user.id
    now = time()

    if user_id in last_command_time and now - last_command_time[user_id] < flood_wait:
        await message.reply_text(f"⚠️ Lütfen {flood_wait} saniye bekleyin.", quote=True)
        return
    last_command_time[user_id] = now

    try:
        combined_data = export_collections_to_json(MONGO_URL)
        if combined_data is None:
            await message.reply_text("⚠️ Koleksiyonlar boş veya bulunamadı.")
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            json.dump(combined_data, tmp, ensure_ascii=False, indent=2, default=str)
            file_path = tmp.name

        await client.send_document(
            chat_id=message.chat.id,
            document=file_path,
            caption="📁 Film ve Dizi Koleksiyonları"
        )

    except Exception as e:
        print(traceback.format_exc())
        await message.reply_text(f"⚠️ Hata: {e}")
