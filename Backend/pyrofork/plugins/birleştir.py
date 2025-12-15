from pyrogram import Client, filters
from pyrogram.types import Message
from Backend.helper.database import Database
from Backend.logger import LOGGER

# Database nesnesi
db = Database()

# Async init fonksiyonu
async def init_db():
    await db.connect()
    LOGGER.info("Database initialized for /ekle plugin")

# Pyrogram startup handler
@Client.on_message(filters.command("ekle") & filters.private)
async def ekle_handler(client: Client, message: Message):
    """
    /ekle komutu ile medya ekleme
    Komut örneği:
    /ekle movie tmdb_id=12345 title="Test Movie" quality="1080p" size="2GB"
    """
    try:
        text = message.text
        # Komut parametrelerini basit parse etme (key=value)
        args = {}
        for part in text.split()[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                args[key] = value

        # Movie ekleme örneği
        if args.get("media_type") == "movie":
            metadata = {
                "tmdb_id": int(args.get("tmdb_id", 0)),
                "imdb_id": args.get("imdb_id"),
                "title": args.get("title"),
                "genres": args.get("genres", "").split(","),
                "description": args.get("description", ""),
                "rate": float(args.get("rate", 0)),
                "year": int(args.get("year", 0)),
                "poster": args.get("poster"),
                "backdrop": args.get("backdrop"),
                "logo": args.get("logo"),
                "cast": args.get("cast", "").split(","),
                "runtime": int(args.get("runtime", 0)),
                "media_type": "movie",
                "quality": args.get("quality"),
                "encoded_string": args.get("encoded_string"),
            }
            result = await db.insert_media(metadata, channel=message.chat.id, msg_id=message.message_id,
                                           size=args.get("size"), name=args.get("title"))
            if result:
                await message.reply(f"✅ Movie '{metadata['title']}' başarıyla eklendi. ID: {result}")
            else:
                await message.reply("❌ Movie eklenemedi.")
        else:
            await message.reply("❌ Sadece movie ekleme destekleniyor şimdilik.")

    except Exception as e:
        LOGGER.error(f"/ekle komut hatası: {e}")
        await message.reply(f"❌ Hata oluştu: {e}")
