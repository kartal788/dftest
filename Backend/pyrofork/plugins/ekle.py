from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from Backend.database import Database
from Backend.helper.modal import MovieSchema, TVShowSchema, QualityDetail, Season, Episode
import os
import aiohttp
import re
from datetime import datetime

TMDB_API_KEY = os.getenv("TMDB_API")
TMDB_BASE = "https://api.themoviedb.org/3"

def parse_tmdb_input(text: str) -> str:
    if text.isdigit():
        return int(text)

    movie_match = re.search(r"/movie/(\d+)", text)
    if movie_match:
        return int(movie_match.group(1))

    tv_match = re.search(r"/tv/(\d+)", text)
    if tv_match:
        return int(tv_match.group(1))

    raise ValueError("Geçersiz TMDB ID veya link")

async def fetch_tmdb(media_type: str, tmdb_id: int) -> dict:
    url = f"{TMDB_BASE}/{media_type}/{tmdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "tr-TR",
        "append_to_response": "credits,images"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as r:
            if r.status != 200:
                raise ValueError("TMDB verisi alınamadı")
            return await r.json()

def map_movie_schema(data: dict, file_link: str, file_name: str, file_size: str) -> MovieSchema:
    return MovieSchema(
        tmdb_id=data["id"],
        imdb_id=data.get("imdb_id"),
        db_index=1,
        title=data["title"],
        genres=[g["name"] for g in data.get("genres", [])],
        description=data.get("overview"),
        rating=data.get("vote_average"),
        release_year=int(data["release_date"][:4]) if data.get("release_date") else None,
        poster=data.get("poster_path"),
        backdrop=data.get("backdrop_path"),
        logo=None,
        cast=[c["name"] for c in data.get("credits", {}).get("cast", [])[:10]],
        runtime=data.get("runtime"),
        media_type="movie",
        telegram=[QualityDetail(
            quality="default",
            id=file_link,
            name=file_name,
            size=file_size
        )]
    )

def map_tv_schema(data: dict, file_link: str, file_name: str, file_size: str) -> TVShowSchema:
    seasons = []
    for s in data.get("seasons", []):
        seasons.append(Season(
            season_number=s["season_number"],
            episodes=[Episode(
                episode_number=1,
                title=s.get("name"),
                episode_backdrop=s.get("poster_path"),
                overview=s.get("overview"),
                released=None,
                telegram=[QualityDetail(
                    quality="default",
                    id=file_link,
                    name=file_name,
                    size=file_size
                )]
            )]
        ))
    return TVShowSchema(
        tmdb_id=data["id"],
        imdb_id=data.get("external_ids", {}).get("imdb_id"),
        db_index=1,
        title=data["name"],
        genres=[g["name"] for g in data.get("genres", [])],
        description=data.get("overview"),
        rating=data.get("vote_average"),
        release_year=int(data["first_air_date"][:4]) if data.get("first_air_date") else None,
        poster=data.get("poster_path"),
        backdrop=data.get("backdrop_path"),
        logo=None,
        cast=[c["name"] for c in data.get("credits", {}).get("cast", [])[:10]],
        runtime=None,
        media_type="tv",
        seasons=seasons
    )

@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_tmdb_file(client, message):
    if len(message.command) < 3:
        await message.reply_text("⚠️ Kullanım: `/ekle <tmdb_id> <dosya_link>`")
        return

    tmdb_id_input = message.command[1]
    file_link = message.command[2]
    file_name = file_link.split("/")[-1]
    file_size = "unknown"

    db = Database()
    await db.connect()

    try:
        # Media type kontrolü
        tmdb_id = parse_tmdb_input(tmdb_id_input)
        # Önce film dene
        try:
            tmdb_data = await fetch_tmdb("movie", tmdb_id)
            schema = map_movie_schema(tmdb_data, file_link, file_name, file_size)
            result = await db.update_movie(schema)
            media_type = "Movie"
        except Exception:
            # Movie başarısız ise TV deneyelim
            tmdb_data = await fetch_tmdb("tv", tmdb_id)
            schema = map_tv_schema(tmdb_data, file_link, file_name, file_size)
            result = await db.update_tv_show(schema)
            media_type = "TV Show"

        if result:
            await message.reply_text(
                f"✅ {media_type} veritabanına kaydedildi / güncellendi\nTMDB ID: `{tmdb_id}`\nDosya: `{file_name}`"
            )
        else:
            await message.reply_text("⚠️ Kayıt başarısız oldu.")

    except Exception as e:
        await message.reply_text(f"❌ Hata:\n`{e}`")
    finally:
        await db.disconnect()
