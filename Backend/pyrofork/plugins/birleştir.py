from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from urllib.parse import quote_plus
import os
import asyncio
import requests
from datetime import datetime

# -------------------- .env yükle --------------------
load_dotenv()
TMDB_API = os.getenv("TMDB_API")
DATABASE_RAW = os.getenv("DATABASE", "")

# -------------------- MongoDB Bağlantısı --------------------
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip()]

if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

raw_mongo_url = db_urls[1]  # İkinci DB (storage)

def encode_mongo_uri(uri):
    if "@" not in uri:
        return uri
    prefix, rest = uri.split("://", 1)
    creds, host = rest.split("@", 1)
    if ":" in creds:
        user, password = creds.split(":", 1)
        user = quote_plus(user)
        password = quote_plus(password)
        return f"{prefix}://{user}:{password}@{host}"
    return uri

MONGO_URL = encode_mongo_uri(raw_mongo_url)
client_db = AsyncIOMotorClient(MONGO_URL)
db = client_db.get_database()
movie_col = db["movie"]
series_col = db["tv"]

print("✅ Storage DB bağlantısı başarıyla kuruldu.")

# -------------------- Onay Bekleyen Kullanıcı --------------------
awaiting_confirmation = {}  # user_id -> asyncio.Task

# -------------------- TMDb'den Veri Çek --------------------
async def fetch_tmdb(tmdb_id, media_type="movie"):
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_API}&language=en-US"
    r = requests.get(url)
    if r.status_code != 200:
        return None
    data = r.json()
    item = {
        "tmdb_id": int(tmdb_id),
        "imdb_id": data.get("imdb_id"),
        "db_index": 1,
        "title": data.get("title") or data.get("name"),
        "genres": [g["name"] for g in data.get("genres", [])],
        "description": data.get("overview"),
        "rating": data.get("vote_average"),
        "release_year": int((data.get("release_date") or data.get("first_air_date") or "0000")[:4]),
        "poster": f"https://images.metahub.space/poster/small/{data.get('imdb_id')}/img",
        "backdrop": f"https://images.metahub.space/background/medium/{data.get('imdb_id')}/img",
        "logo": f"https://images.metahub.space/logo/medium/{data.get('imdb_id')}/img",
        "cast": [],
        "runtime": f"{data.get('runtime') or data.get('episode_run_time', [0])[0]} min",
        "media_type": media_type,
        "updated_on": str(datetime.now()),
        "telegram": []
    }
    return item

# -------------------- /ekle Komutu --------------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_link(client, message):
    if len(message.command) < 3:
        return await message.reply_text("❌ Kullanım: /ekle <pixeldrain_link> <tmdb_id>")

    link = message.command[1]
    tmdb_id = message.command[2]

    media_item = await fetch_tmdb(tmdb_id)
    if not media_item:
        return await message.reply_text("❌ TMDb verisi bulunamadı.")

    # Telegram kaydı ekle
    media_item["telegram"].append({
        "quality": "1080p",
        "id": link.replace("https://pixeldrain.com/u/", "https://pixeldrain.com/api/file/"),
        "name": link.split("/")[-1],
        "size": "Unknown"
    })

    collection = movie_col if media_item["media_type"] == "movie" else series_col
    await collection.insert_one(media_item)

    await message.reply_text(f"✅ {media_item['title']} veritabanına eklendi.")

# -------------------- /sil Komutu --------------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client, message):
    user_id = message.from_user.id
    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\n"
        "Onaylamak için **Evet**, iptal için **Hayır** yazın.\n"
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

# -------------------- Onay Mesajı --------------------
@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    if text == "evet":
        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(
            f"✅ Silindi.\nFilmler: {movie_count}\nDiziler: {series_count}"
        )
    elif text == "hayır":
        await message.reply_text("❌ Silme işlemi iptal edildi.")
