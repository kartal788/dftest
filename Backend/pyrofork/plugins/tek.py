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

MONGO_URL = db_urls[1]  # İkinci database
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
    else:
        return None

def pixeldrain_to_api(url: str) -> str:
    """
    Pixeldrain linkini API formatına çevirir:
    https://pixeldrain.com/u/6Hk -> https://pixeldrain.com/api/file/6Hk
    """
    match = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    if match:
        file_id = match.group(1)
        return f"https://pixeldrain.com/api/file/{file_id}"
    return url

# ----------------- /ekle Komutu -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client: Client, message: Message):
    await init_db()
    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = message.command[1]
    url = pixeldrain_to_api(url)  # Pixeldrain linklerini API formatına çevir
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

    # Encode string
    data = {"chat_id": message.chat.id, "msg_id": message.id}
    try:
        encoded_string = await encode_string(data)
    except Exception:
        encoded_string = None

    # TMDb arama
    async with API_SEMAPHORE:
        if season and episode:
            search_result = await tmdb.search().tv(query=title)
        else:
            search_result = await tmdb.search().movies(query=title, year=year)

    if not search_result:
        await message.reply_text(f"{title} için TMDb sonucu bulunamadı.")
        return

    metadata = search_result[0]

    # TMDb detay çekme ve kayıt oluşturma
    if season:
        details = await tmdb.tv(metadata.id).details()
        cast = [c.name for c in getattr(details, "cast", [])[:5]]
        genres = [g.name for g in getattr(details, "genres", [])]
        release_year = get_year(getattr(metadata, "first_air_date", None))
        record = {
            "tmdb_id": metadata.id,
            "imdb_id": getattr(metadata, "imdb_id", ""),
            "db_index": 1,
            "title": title,
            "genres": genres,
            "description": getattr(metadata, "overview", ""),
            "rating": getattr(metadata, "vote_average", 0),
            "release_year": release_year,
            "poster": f"https://image.tmdb.org/t/p/w500{getattr(metadata, 'poster_path', '')}",
            "backdrop": f"https://image.tmdb.org/t/p/w780{getattr(metadata, 'backdrop_path', '')}",
            "logo": f"https://image.tmdb.org/t/p/w300{getattr(metadata, 'logo', '')}",
            "cast": cast,
            "runtime": f"{getattr(details, 'episode_run_time', ['?'])[0]} min",
            "media_type": "tv",
            "updated_on": str(datetime.utcnow()),
            "seasons": [{
                "season_number": season,
                "episodes": [{
                    "episode_number": episode,
                    "title": filename,
                    "overview": getattr(metadata, 'overview', ''),
                    "telegram": [{
                        "quality": quality,
                        "id": url,
                        "name": filename,
                        "size": "UNKNOWN"
                    }]
                }]
            }],
        }
        collection = series_col
    else:
        details = await tmdb.movie(metadata.id).details()
        cast = [c.name for c in getattr(details, "cast", [])[:5]]
        genres = [g.name for g in getattr(details, "genres", [])]
        release_year = get_year(getattr(metadata, "release_date", None))
        record = {
            "tmdb_id": metadata.id,
            "imdb_id": getattr(metadata, "imdb_id", ""),
            "db_index": 1,
            "title": title,
            "genres": genres,
            "description": getattr(metadata, "overview", ""),
            "rating": getattr(metadata, "vote_average", 0),
            "release_year": release_year,
            "poster": f"https://image.tmdb.org/t/p/w500{getattr(metadata, 'poster_path', '')}",
            "backdrop": f"https://image.tmdb.org/t/p/w780{getattr(metadata, 'backdrop_path', '')}",
            "logo": f"https://image.tmdb.org/t/p/w300{getattr(metadata, 'logo', '')}",
            "cast": cast,
            "runtime": f"{getattr(details, 'runtime', '?')} min",
            "media_type": "movie",
            "updated_on": str(datetime.utcnow()),
            "telegram": [{
                "quality": quality,
                "id": url,
                "name": filename,
                "size": "UNKNOWN"
            }],
        }
        collection = movie_col

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
        awaiting_confirmation[user_id].cancel()

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

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    await init_db()  # İkinci DB bağlanmış olacak
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
