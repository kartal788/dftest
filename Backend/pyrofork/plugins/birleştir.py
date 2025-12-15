import os
import re
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp

# ------------------ ENV ------------------
TMDB_API = os.getenv("TMDB_API", "")
MONGO_URL = os.getenv("DATABASE", "").split(",")[1]  # ikinci database

# ------------------ MONGO ------------------
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

# ------------------ Onay Bekleyen ------------------
awaiting_confirmation = {}

# ------------------ TMDB API ------------------
async def fetch_tmdb_movie(title, year=None):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API}&query={title}"
    if year:
        url += f"&year={year}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            if data.get("results"):
                return data["results"][0]
    return None

# ------------------ /ekle ------------------
@Client.on_message(filters.command("ekle") & filters.private)
async def add_link(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Lütfen bir link gönderin: /ekle <link>")

    full_text = " ".join(message.command[1:])
    match = re.match(r"(https?://\S+)\s+(.+)", full_text)
    if not match:
        return await message.reply_text("❌ Format yanlış. Örnek: /ekle <link> <dosya_adı>")
    
    link, file_name = match.groups()
    # Dosya adından film/dizi adı ve yılı tahmini
    file_name_clean = file_name.replace(".", " ").replace("_", " ")
    title_year_match = re.match(r"(.+?)\s(\d{4})", file_name_clean)
    if not title_year_match:
        return await message.reply_text("❌ Dosya adından başlık ve yıl çıkarılamadı.")
    
    title, year = title_year_match.groups()
    await init_db()
    tmdb_data = await fetch_tmdb_movie(title.strip(), year)
    if not tmdb_data:
        return await message.reply_text(f"❌ '{title}' için TMDb verisi bulunamadı.")

    # MongoDB için kayıt yapısı
    movie_doc = {
        "tmdb_id": tmdb_data["id"],
        "imdb_id": tmdb_data.get("imdb_id", ""),
        "db_index": 1,
        "title": tmdb_data["title"],
        "genres": [g["name"] for g in tmdb_data.get("genres", [])],
        "description": tmdb_data.get("overview", ""),
        "rating": tmdb_data.get("vote_average", 0),
        "release_year": int(year),
        "poster": f"https://images.metahub.space/poster/small/{tmdb_data.get('imdb_id','')}/img",
        "backdrop": f"https://images.metahub.space/background/medium/{tmdb_data.get('imdb_id','')}/img",
        "logo": f"https://images.metahub.space/logo/medium/{tmdb_data.get('imdb_id','')}/img",
        "cast": [],
        "runtime": f"{tmdb_data.get('runtime', 0)} min",
        "media_type": "movie",
        "updated_on": str(datetime.utcnow()),
        "telegram": [
            {
                "quality": "1080p",
                "id": link,
                "name": file_name,
                "size": "Unknown"
            }
        ]
    }
    await movie_col.insert_one(movie_doc)
    await message.reply_text(f"✅ '{title}' MongoDB’ye eklendi!")

# ------------------ /sil ------------------
@Client.on_message(filters.command("sil") & filters.private)
async def request_delete(client, message):
    user_id = message.from_user.id
    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\nOnaylamak için 'Evet', iptal için 'Hayır' yazın.\n⏱ 60 saniye içinde cevap vermezsen işlem iptal edilir."
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

# ------------------ Onay Mesajı ------------------
@Client.on_message(filters.private & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return
    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    if text == "evet":
        await init_db()
        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(f"✅ Silindi: Filmler {movie_count}, Diziler {series_count}")
    elif text == "hayır":
        await message.reply_text("❌ Silme işlemi iptal edildi.")
