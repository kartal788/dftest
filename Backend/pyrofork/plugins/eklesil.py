import os
import re
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient
from themoviedb import aioTMDb
import PTN
import aiohttp
from Backend.helper.custom_filter import CustomFilters

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb+srv")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

# ----------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(12)
awaiting_confirmation = {}

# ----------------- Helpers -----------------
def safe(obj, attr, default=None):
    return getattr(obj, attr, default) or default

def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except:
        return None

def clean_title(title: str):
    title = re.sub(r"[._]", " ", title)
    title = re.sub(r"\b(1080p|720p|bluray|brrip|x264|x265|hevc|tr|eng|dual|multi|dts|aac)\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip()
    return title

def title_variants(title: str):
    parts = title.split()
    variants = [title]
    if len(parts) > 1:
        variants.append(parts[-1])  # İngilizce isim genelde sonda
    return list(dict.fromkeys(variants))

def pixeldrain_to_api(url):
    m = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url

async def head(url, key):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True) as r:
                return r.headers.get(key)
    except:
        return None

async def filename_from_url(url):
    cd = await head(url, "Content-Disposition")
    if cd:
        m = re.search(r'filename="(.+?)"', cd)
        if m:
            return m.group(1)
    return url.split("/")[-1]

async def filesize(url):
    size = await head(url, "Content-Length")
    if not size:
        return "YOK"
    try:
        size = int(size)
        for u in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.2f} {u}"
            size /= 1024
    except:
        return "YOK"

def build_media_record(meta, details, display_name, url, quality, media_type, season=None, episode=None):
    base = {
        "tmdb_id": meta.id,
        "title": safe(meta, "title", safe(meta, "name")),
        "description": safe(meta, "overview", ""),
        "rating": safe(meta, "vote_average", 0),
        "release_year": year_from(safe(meta, "release_date", safe(meta, "first_air_date"))),
        "poster": f"https://image.tmdb.org/t/p/w500{safe(meta,'poster_path','')}",
        "updated_on": str(datetime.utcnow()),
    }

    if media_type == "movie":
        return {
            **base,
            "media_type": "movie",
            "telegram": [{
                "quality": quality,
                "id": url,
                "name": display_name,
                "size": "YOK"
            }]
        }

    return {
        **base,
        "media_type": "tv",
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode,
                "title": display_name,
                "telegram": [{
                    "quality": quality,
                    "id": url,
                    "name": display_name,
                    "size": "YOK"
                }]
            }]
        }]
    }

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle link [Özel İsim]")

    pairs, current = [], []
    for arg in args:
        if arg.startswith("http"):
            if current:
                pairs.append(current)
            current = [arg]
        else:
            current.append(arg)
    if current:
        pairs.append(current)

    inputs = [(pixeldrain_to_api(p[0]), " ".join(p[1:]).strip() if len(p) > 1 else None) for p in pairs]

    success, failed = [], []
    msg = await message.reply_text("📥 İşlem başlatıldı...")

    for i, (raw, custom_name) in enumerate(inputs, 1):
        display_name = custom_name or raw
        try:
            filename = await filename_from_url(raw)
            parsed = PTN.parse(filename)

            raw_title = parsed.get("title")
            if not raw_title:
                raise Exception("Başlık çözümlenemedi")

            title = clean_title(raw_title)
            variants = title_variants(title)

            year = parsed.get("year")
            quality = parsed.get("resolution") or "UNKNOWN"
            size = await filesize(raw)
            display_name = custom_name or filename

            results = []
            async with API_SEMAPHORE:
                for q in variants:
                    results = await tmdb.search().movies(query=q, year=year)
                    if results:
                        break
                if not results:
                    for q in variants:
                        results = await tmdb.search().movies(query=q)
                        if results:
                            break

            if not results:
                raise Exception("TMDB sonucu bulunamadı")

            meta = results[0]
            details = await tmdb.movie(meta.id).details()

            if not await movie_col.find_one({"tmdb_id": meta.id}):
                doc = build_media_record(meta, details, display_name, raw, quality, "movie")
                doc["telegram"][0]["size"] = size
                await movie_col.insert_one(doc)

            success.append(display_name)

        except Exception as e:
            failed.append((display_name, str(e)))

        await msg.edit_text(f"🔄 {i}/{len(inputs)} | ✅ {len(success)} | ❌ {len(failed)}")

    failed_list = "\n".join(f"• {n}\n   ↳ ❗ {r}" for n, r in failed) or "• Yok"
    success_list = "\n".join(f"• {x}" for x in success) or "• Yok"

    await msg.edit_text(
        f"📊 **İşlem Tamamlandı**\n\n"
        f"✅ Başarılı:\n{success_list}\n\n"
        f"❌ Başarısız (Nedenleriyle):\n{failed_list}"
    )

# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    awaiting_confirmation[message.from_user.id] = True
    await message.reply_text("⚠️ Onay için **Evet**, iptal için **Hayır** yaz.")

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return
    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("✅ Tüm veriler silindi")
    else:
        await message.reply_text("❌ İptal edildi")
