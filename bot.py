import asyncio
import os
import json
import html
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile,
)
from aiogram.filters import Command
import yt_dlp

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

PLAYLISTS_FILE = "playlists.json"
DOWNLOAD_DIR = "downloads"
COOKIES_FILE = os.getenv("COOKIES_FILE")  # необязательно: путь к cookies.txt


def load_playlists():
    if os.path.exists(PLAYLISTS_FILE):
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_playlists(data):
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb"]}},
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


TITLES = {}  # id -> название, для запасного поиска на SoundCloud


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🎵 <b>Музыкальный бот готов к работе!</b>\n\n"
        "• Отправь название трека или исполнителя "
        "(например: <i>АлСми - Рюмочка водочки</i>).\n"
        "• /playlist — сохранённые треки."
    )


@dp.message(Command("playlist"))
async def cmd_playlist(message: Message):
    user_id = str(message.from_user.id)
    playlists = load_playlists()

    if not playlists.get(user_id):
        await message.answer("📭 Твой плейлист пока пуст.")
        return

    text = "📂 <b>Твой плейлист:</b>\n\n"
    keyboard = []
    for idx, track in enumerate(playlists[user_id]):
        title = track["title"]
        TITLES[track["id"]] = title
        text += f"{idx + 1}. {html.escape(title)}\n"
        keyboard.append([InlineKeyboardButton(
            text=f"▶️ {title[:30]}", callback_data=f"dl_{track['id']}"
        )])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@dp.message(F.text & ~F.text.startswith("/"))
async def search_music(message: Message):
    query = message.text.strip()
    waiting_msg = await message.answer(f"🔍 Ищу: <i>{html.escape(query)}</i>...")

    def search():
        opts = base_opts()
        opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        return info.get("entries", []) if info else []

    try:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(None, search)
        entries = [e for e in entries if e and e.get("id")]

        if not entries:
            await waiting_msg.edit_text("❌ Ничего не найдено. Попробуй изменить запрос.")
            return

        keyboard = []
        for e in entries[:5]:
            title = e.get("title") or "Без названия"
            TITLES[e["id"]] = title
            keyboard.append([InlineKeyboardButton(
                text=f"📥 {title[:50]}", callback_data=f"dl_{e['id']}"
            )])

        await waiting_msg.edit_text(
            "🎵 Выбери трек:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        await waiting_msg.edit_text(f"⚠️ Ошибка поиска: {html.escape(str(e))[:300]}")


@dp.callback_query(F.data.startswith("dl_"))
async def process_download(callback: CallbackQuery):
    video_id = callback.data[3:]
    await callback.answer()
    await callback.message.edit_text("📥 Скачиваю и конвертирую трек...")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = base_opts()
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/{video_id}.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    })

    def run(target):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
        if info and info.get("entries"):
            info = info["entries"][0]
        return info

    def download():
        try:
            info = run(video_url(video_id))
        except Exception as yt_err:
            # YouTube блокирует IP -> пробуем SoundCloud по названию
            title = TITLES.get(video_id)
            if not title:
                raise yt_err
            info = run(f"scsearch1:{title}")
        return (
            os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3"),
            info.get("title", "Track"),
            info.get("uploader"),
        )

    file_path = None
    try:
        loop = asyncio.get_running_loop()
        file_path, title, performer = await loop.run_in_executor(None, download)

        if not os.path.exists(file_path):
            await callback.message.edit_text("❌ Не удалось скачать файл.")
            return

        if os.path.getsize(file_path) > 49 * 1024 * 1024:
            await callback.message.edit_text("❌ Файл больше 50 МБ — Telegram не даст отправить.")
            return

        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❤️ В мой плейлист", callback_data=f"add_{video_id}")
        ]])
        await callback.message.answer_audio(
            audio=FSInputFile(file_path),
            title=title,
            performer=performer,
            caption=f"🎧 {html.escape(title)}",
            reply_markup=markup,
        )
        await callback.message.delete()
    except Exception as e:
        await callback.message.edit_text(f"⚠️ Ошибка скачивания: {html.escape(str(e))[:300]}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


@dp.callback_query(F.data.startswith("add_"))
async def add_to_playlist(callback: CallbackQuery):
    video_id = callback.data[4:]
    user_id = str(callback.from_user.id)

    # название берём из подписи аудио — без лишнего запроса к YouTube
    title = (callback.message.caption or "Любимый трек").replace("🎧 ", "", 1)

    playlists = load_playlists()
    playlists.setdefault(user_id, [])

    if any(t["id"] == video_id for t in playlists[user_id]):
        await callback.answer("ℹ️ Этот трек уже есть в плейлисте.", show_alert=True)
        return

    playlists[user_id].append({"id": video_id, "title": title})
    save_playlists(playlists)
    await callback.answer("✅ Трек добавлен в плейлист!", show_alert=True)


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
