import asyncio
import glob
import os
import re
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


def clean_title(t: str) -> str:
    """Убирает из названия мусор вроде (official audio), [HD], 'клип'."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = re.sub(r"(?i)\b(official|audio|video|lyrics|hd|hq|клип|премьера)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—|")


def soundcloud_urls(title: str):
    """Ищет трек на SoundCloud, возвращает список ссылок."""
    urls = []
    for q in dict.fromkeys([clean_title(title), title]):
        if not q:
            continue
        try:
            opts = base_opts()
            opts["extract_flat"] = True
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch5:{q}", download=False)
            for e in (info or {}).get("entries") or []:
                u = e.get("url") or e.get("webpage_url")
                if u and u not in urls:
                    urls.append(u)
        except Exception as err:
            print(f"[SoundCloud search error] {q!r}: {err}", flush=True)
        if urls:
            break
    print(f"[SoundCloud] found: {urls}", flush=True)
    return urls


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

    def find_files():
        found = [f for f in glob.glob(f"{DOWNLOAD_DIR}/{video_id}.*")
                 if not f.endswith((".part", ".ytdl"))]
        found.sort(key=lambda f: 0 if f.endswith(".mp3") else 1)
        return found

    def download():
        source = "YouTube"
        try:
            info = run(video_url(video_id))
        except Exception as yt_err:
            yt_msg = str(yt_err).strip().split("\n")[0][:150]
            print(f"[YouTube error] {yt_err}", flush=True)
            # YouTube блокирует IP -> пробуем SoundCloud по названию
            title = TITLES.get(video_id)
            if not title:
                raise yt_err
            source = "SoundCloud"
            urls = soundcloud_urls(title)
            if not urls:
                raise RuntimeError(f"YouTube не пускает, а на SoundCloud ничего не найдено. ({yt_msg})")
            info, last_err = None, None
            for u in urls[:5]:
                try:
                    info = run(u)
                except Exception as e:
                    last_err = e
                    print(f"[SoundCloud download error] {u}: {e}", flush=True)
                    continue
                if find_files():
                    break
            else:
                raise RuntimeError(f"SoundCloud не скачал: {str(last_err)[:150] or 'нет файла'}")

        found = find_files()
        print(f"[{source}] files: {found}", flush=True)
        if not found:
            raise RuntimeError(f"файл не появился (источник: {source})")
        return found[0], (info or {}).get("title", "Track"), (info or {}).get("uploader")

    file_path = None
    try:
        loop = asyncio.get_running_loop()
        file_path, title, performer = await loop.run_in_executor(None, download)

        if os.path.getsize(file_path) > 49 * 1024 * 1024:
            await callback.message.edit_text("❌ Файл больше 50 МБ — Telegram не даст отправить.")
            return

        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❤️ В мой плейлист", callback_data=f"add_{video_id}")
        ]])
        if file_path.endswith((".mp3", ".m4a")):
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                title=title,
                performer=performer,
                caption=f"🎧 {html.escape(title)}",
                reply_markup=markup,
            )
        else:
            await callback.message.answer_document(
                document=FSInputFile(file_path),
                caption=f"🎧 {html.escape(title)}",
                reply_markup=markup,
            )
        await callback.message.delete()
    except Exception as e:
        print(f"[download error] {e!r}", flush=True)
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
