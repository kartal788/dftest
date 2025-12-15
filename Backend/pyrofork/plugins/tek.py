import os
import re
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient
from themoviedb import aioTMDb
import PTN
from Backend.helper.encrypt import encode_string
from Backend.helper.custom_filter import CustomFilters

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip() and u.strip().startswith("mongodb+srv")]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE URL bulunamadı!")

MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
if not TMDB_API:
    raise Exception("TMDB_API bulunamadı!")

# ----------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

async def init_db():
    global db, movie_col, series_col
    db = client[DB_NAME]
    movie_col = db["movie"]
    series_col = db["tv"]

# ----------------- TMDb -----------------
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")
API_SEMAPHORE = asyncio.Semaphore(12)

# ----------------- Onay Bekleyen -----------------
awaiting_confirmation = {}

# ----------------- Yardımcı Fonksiyonlar -----------------
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
    else:  # TV series
        episode_runtime_list = safe_getattr(details, "episode_run_time", [])
        runtime = f"{episode_runtime_list[0]} min" if episode_runtime_list else "UNKNOWN"

        # Bölüm posteri ve yayın tarihi
        episode_release_date = safe_getattr(metadata, "air_date", None)
        episode_backdrop = safe_getattr(metadata, "still_path", "")

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
                    "released": episode_release_date,
                    "episode_backdrop": f"https://image.tmdb.org/t/p/w780{episode_backdrop}",
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

# ----------------- /ekle Komutu -----------------
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
        if season and episode:  # TV dizisi ve bölüm
            search_result = await tmdb.search().tv(query=title)
            collection = series_col
            media_type = "tv"

            if not search_result:
                await message.reply_text(f"{title} için TMDb sonucu bulunamadı.")
                return

            show_metadata = search_result[0]
            show_details = await tmdb.tv(show_metadata.id).details()
            # Düzeltilmiş bölüm verisi alma
            episode_metadata = await tmdb.tv_episode(show_metadata.id, season, episode).details()

            record = build_media_record(
                metadata=episode_metadata,
                details=show_details,
                filename=filename,
                url=url,
                quality=quality,
                media_type="tv",
                season=season,
                episode=episode
            )

        else:  # Film
            search_result = await tmdb.search().movies(query=title, year=year)
            collection = movie_col
            media_type = "movie"

            if not search_result:
                await message.reply_text(f"{title} için TMDb sonucu bulunamadı.")
                return

            metadata = search_result[0]
            details = await tmdb.movie(metadata.id).details()
            record = build_media_record(
                metadata=metadata,
                details=details,
                filename=filename,
                url=url,
                quality=quality,
                media_type="movie"
            )

    await collection.insert_one(record)
    await message.reply_text(f"✅ {title} başarıyla eklendi.")

# ----------------- /sil Komutu -----------------
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
