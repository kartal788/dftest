import os
import re
import json
import asyncio
from time import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from themoviedb import aioTMDb
import PTN
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.encrypt import encode_string

# ---------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip() and u.strip().startswith("mongodb+srv")]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE URL bulunamadı!")

MONGO_URL = db_urls[1]  # İkinci database
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
if not TMDB_API:
    raise Exception("TMDB_API bulunamadı!")

# ---------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

async def init_db():
    global db, movie_col, series_col
    db = client[DB_NAME]
    movie_col = db["movie"]
    series_col = db["tv"]

# ---------------- TMDb -----------------
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")
API_SEMAPHORE = asyncio.Semaphore(12)

# ---------------- Onay Bekleyen -----------------
awaiting_confirmation = {}

# ---------------- Yardımcı Fonksiyonlar -----------------
def get_year(date_obj):
    if isinstance(date_obj, str):
        try:
            return int(date_obj.split("-")[0])
        except:
            return None
    elif hasattr(date_obj, "year"):
        return date_obj.year
    return None

def pixeldrain_to_api(url: str) -> str:
    match = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    if match:
        file_id = match.group(1)
        return f"https://pixeldrain.com/api/file/{file_id}"
    return url

def safe_getattr(obj, attr, default=None):
    return getattr(obj, attr, default) or default

def build_media_record(metadata, details, filename, url, quality, media_type, season=None, episode=None):
    title = safe_getattr(metadata, "title", safe_getattr(metadata, "name", filename))
    release_date = safe_getattr(metadata, "release_date", safe_getattr(metadata, "first_air_date"))
    release_year = get_year(release_date)
    genres = [g.name for g in safe_getattr(details, "genres", [])]
    cast = [c.name for c in safe_getattr(details, "cast", [])[:5]]
    poster = safe_getattr(metadata, "poster_path", "")
    backdrop = safe_getattr(metadata, "backdrop_path", "")
    logo = safe_getattr(metadata, "logo", "")

    if media_type == "movie":
        runtime_val = safe_getattr(details, "runtime")
        runtime = f"{runtime_val} min" if runtime_val else "UNKNOWN"
        record = {
            "tmdb_id": metadata.id,
            "imdb_id": safe_getattr(metadata, "imdb_id", ""),
            "db_index": 1,
            "title": title,
            "genres": genres,
            "description": safe_getattr(metadata, "overview", ""),
            "rating": safe_getattr(metadata, "vote_average", 0),
            "release_year": release_year,
            "poster": f"https://image.tmdb.org/t/p/w500{poster}",
            "backdrop": f"https://image.tmdb.org/t/p/w780{backdrop}",
            "logo": f"https://image.tmdb.org/t/p/w300{logo}",
            "cast": cast,
            "runtime": runtime,
            "media_type": "movie",
            "updated_on": str(datetime.utcnow()),
            "telegram": [{
                "quality": quality,
                "id": url,
                "name": filename,
                "size": "UNKNOWN"
            }],
        }
    else:
        episode_runtime_list = safe_getattr(details, "episode_run_time", [])
        runtime = f"{episode_runtime_list[0]} min" if episode_runtime_list else "UNKNOWN"

        record = {
            "tmdb_id": metadata.id,
            "imdb_id": safe_getattr(metadata, "imdb_id", ""),
            "db_index": 1,
            "title": title,
            "genres": genres,
            "description": safe_getattr(metadata, "overview", ""),
            "rating": safe_getattr(metadata, "vote_average", 0),
            "release_year": release_year,
            "poster": f"https://image.tmdb.org/t/p/w500{poster}",
            "backdrop": f"https://image.tmdb.org/t/p/w780{backdrop}",
            "logo": f"https://image.tmdb.org/t/p/w300{logo}",
            "cast": cast,
            "runtime": runtime,
            "media_type": "tv",
            "updated_on": str(datetime.utcnow()),
            "seasons": [{
                "season_number": season,
                "episodes": [{
                    "episode_number": episode,
                    "title": filename,
                    "overview": safe_getattr(metadata, "overview", ""),
                    "telegram": [{
                        "quality": quality,
                        "id": url,
                        "name": filename,
                        "size": "UNKNOWN"
                    }]
                }]
            }]
        }
    return record

# ---------------- /ekle Komutu -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client: Client, message: Message):
    await init_db()
    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = pixeldrain_to_api(message.command[1])
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

    async with API_SEMAPHORE:
        if season and episode:
            search_result = await tmdb.search().tv(query=title)
            collection = series_col
            media_type = "tv"
        else:
            search_result = await tmdb.search().movies(query=title, year=year)
            collection = movie_col
            media_type = "movie"

    if not search_result:
        await message.reply_text(f"{title} için TMDb sonucu bulunamadı.")
        return

    metadata = search_result[0]
    details = await (tmdb.tv(metadata.id).details() if media_type == "tv" else tmdb.movie(metadata.id).details())
    record = build_media_record(metadata, details, filename, url, quality, media_type, season, episode)
    
    # Duplicate kontrol ve güncelleme
    await collection.update_one(
        {"tmdb_id": metadata.id},
        {"$push": {"telegram": record["telegram"][0]}},
        upsert=True
    )

    await message.reply_text(f"✅ {title} başarıyla eklendi.")

# ---------------- /sil Komutu -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client: Client, message: Message):
    user_id = message.from_user.id
    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\n"
        "Onaylamak için **Evet**, iptal etmek için **Hayır** yazın.\n"
        "⏱ 60 saniye içinde cevap vermezsen işlem iptal edilir."
    )

    if user_id in awaiting_confirmation:
        task = awaiting_confirmation.pop(user_id, None)
        if task:
            task.cancel()

    async def timeout():
        await asyncio.sleep(60)
        if user_id in awaiting_confirmation:
            awaiting_confirmation.pop(user_id, None)
            await message.reply_text("⏰ Zaman doldu, silme işlemi iptal edildi.")

    task = asyncio.create_task(timeout())
    awaiting_confirmation[user_id] = task

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    task = awaiting_confirmation.pop(user_id, None)
    if task:
        task.cancel()

    text = message.text.strip().lower()
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

# ---------------- /vindir Komutu -----------------
flood_wait = 30  # saniye
last_command_time = {}  # kullanıcı_id : zaman

def export_collections_to_json(url):
    client_sync = MongoClient(url)
    db_name_list = client_sync.list_database_names()
    if not db_name_list:
        return None
    db_sync = client_sync[db_name_list[0]]
    movie_data = list(db_sync["movie"].find({}, {"_id": 0}))
    tv_data = list(db_sync["tv"].find({}, {"_id": 0}))
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

        file_path = "/tmp/dizi_ve_film_veritabanı.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(combined_data, f, ensure_ascii=False, indent=2, default=str)

        await client.send_document(
            chat_id=message.chat.id,
            document=file_path,
            caption="📁 Film ve Dizi Koleksiyonları"
        )
    except Exception as e:
        await message.reply_text(f"⚠️ Hata: {e}")
        print("vindir hata:", e)
