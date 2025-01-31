import logging
import re
import os
import time
import random
import json
import requests
import glob
import shutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)
import instaloader
from instaloader import Instaloader, Profile
from instaloader.exceptions import LoginRequiredException, QueryReturnedBadRequestException
from dotenv import load_dotenv
from requests.cookies import RequestsCookieJar

load_dotenv()

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== LOAD CONFIGURATION ==========
REQUIRED_ENV_VARS = [
    'TOKEN_BOT', 'INSTAGRAM_SESSIONID', 'INSTAGRAM_DS_USER_ID',
    'INSTAGRAM_CSRFTOKEN', 'INSTAGRAM_RUR', 'INSTAGRAM_MID', 'INSTAGRAM_USERNAME'
]

def clean_cookie_value(value: str) -> str:
    return value.strip().strip('"').strip("'") if value else None

env_vars = {var: clean_cookie_value(os.getenv(var)) for var in REQUIRED_ENV_VARS}

if any(value is None for value in env_vars.values()):
    missing = [var for var, val in env_vars.items() if val is None]
    logger.error(f"❌ Missing .env variables: {', '.join(missing)}")
    exit(1)

# ========== USER AGENT MANAGEMENT ==========
def load_user_agents():
    try:
        with open("user-agents.json", "r", encoding="utf-8") as f:
            agents = json.load(f)
            return [ua for ua in agents if isinstance(ua, str) and ua.strip()]
    except Exception as e:
        logger.error(f"❌ Error loading user agents: {str(e)}")
        exit(1)

USER_AGENTS = load_user_agents()

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Cookie": f"sessionid={env_vars['INSTAGRAM_SESSIONID']}",
        "X-CSRFToken": env_vars['INSTAGRAM_CSRFTOKEN']
    }

# ========== INSTAGRAM SETUP ==========
loader = Instaloader(
    user_agent=random.choice(USER_AGENTS),
    sleep=True,
    quiet=True,
    request_timeout=30
)

try:
    # Buat cookie jar
    cookie_jar = RequestsCookieJar()
    cookies = {
        "sessionid": env_vars['INSTAGRAM_SESSIONID'],
        "ds_user_id": env_vars['INSTAGRAM_DS_USER_ID'],
        "csrftoken": env_vars['INSTAGRAM_CSRFTOKEN'],
        "rur": env_vars['INSTAGRAM_RUR'],
        "mid": env_vars['INSTAGRAM_MID']
    }
    
    # Tambahkan cookies ke session
    for name, value in cookies.items():
        cookie_jar.set(name, value, domain='.instagram.com', path='/')
    
    loader.context._session.cookies = cookie_jar
    loader.context.username = env_vars['INSTAGRAM_USERNAME']
    
    # Verifikasi session
    test_profile = Profile.from_username(loader.context, env_vars['INSTAGRAM_USERNAME'])
    logger.info(f"✅ Login berhasil sebagai: {test_profile.full_name}")

except Exception as e:
    logger.error(f"❌ Gagal login: {str(e)}")
    exit(1)

# ========== BOT HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📸 Kirim URL profil Instagram untuk melihat:\n"
        "- Foto Profil HD\n"
        "- Story Terbaru\n"
        "- Highlight\n"
        "- Info Profil\n\n"
        "Contoh URL: https://www.instagram.com/nasa/"
    )

def extract_username(url: str) -> str:
    match = re.match(
        r"(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)/?",
        url,
        re.IGNORECASE
    )
    return match.group(1) if match else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text.strip()
    username = extract_username(url)
    
    if not username:
        await update.message.reply_text("❌ Format URL tidak valid!")
        return

    try:
        context.user_data['current_profile'] = username
        
        # Buat inline keyboard
        keyboard = [
            [
                InlineKeyboardButton("📷 Foto Profil", callback_data='profile_pic'),
                InlineKeyboardButton("📹 Story", callback_data='story')
            ],
            [
                InlineKeyboardButton("🌟 Highlights", callback_data='highlights'),
                InlineKeyboardButton("📊 Info Profil", callback_data='profile_info')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Pilih fitur untuk @{username}:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        await update.message.reply_text("⚠️ Terjadi kesalahan, coba lagi nanti")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    username = context.user_data.get('current_profile')
    if not username:
        await query.edit_message_text("❌ Session expired, silakan kirim URL lagi")
        return

    try:
        if query.data == 'profile_pic':
            await handle_profile_pic(query, username)
            
        elif query.data == 'story':
            await handle_stories(query, username)
            
        elif query.data == 'highlights':
            await handle_highlights(query, username)
            
        elif query.data == 'profile_info':
            await handle_profile_info(query, username)
            
        elif query.data.startswith('highlight_'):
            highlight_id = query.data.split('_')[1]
            await handle_highlight_items(query, username, highlight_id)
            
    except Exception as e:
        logger.error(f"Error in button handler: {str(e)}", exc_info=True)
        await query.edit_message_text("⚠️ Gagal memproses permintaan")

async def handle_profile_pic(query, username):
    try:
        profile = Profile.from_username(loader.context, username)
        
        if profile.is_private and not profile.followed_by_viewer:
            await query.message.reply_text("🔒 Profil privat - Anda belum follow akun ini")
            return

        # Dapatkan URL HD
        hd_url = profile.profile_pic_url.replace("/s150x150/", "/s1080x1080/")
        
        # Download gambar
        response = requests.get(hd_url, headers=get_random_headers(), stream=True)
        response.raise_for_status()

        # Simpan sementara
        temp_file = f"temp_{username}_{int(time.time())}.jpg"
        with open(temp_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Kirim sebagai dokumen
        await query.message.reply_document(
            document=open(temp_file, "rb"),
            filename=f"{username}_profile.jpg",
            caption=f"📸 Foto Profil @{username}"
        )
        os.remove(temp_file)

    except Exception as e:
        logger.error(f"Profile pic error: {str(e)}")
        await query.message.reply_text("⚠️ Gagal mengambil foto profil")

async def handle_stories(query, username):
    try:
        profile = Profile.from_username(loader.context, username)

        if profile.is_private and not profile.followed_by_viewer:
            await query.message.reply_text("🔒 Profil privat - Anda belum follow akun ini")
            return

        stories = []
        for story in loader.get_stories([profile.userid]):
            stories.extend(story.get_items())

        stories.sort(key=lambda x: x.date_utc)

        if not stories:
            await query.message.reply_text("📭 Tidak ada story yang tersedia")
            return

        temp_dir = f"temp_{username}_{int(time.time())}"
        os.makedirs(temp_dir, exist_ok=True)

        try:
            sent_count = 0
            logger.info(f"🔄 Memproses {len(stories)} story untuk @{username}")

            for story_item in stories:
                try:
                    if story_item.is_video:
                        loader.download_storyitem(story_item, temp_dir)
                        time.sleep(3)

                        downloaded_files = [
                            f for f in os.listdir(temp_dir)
                            if f.endswith(".mp4")
                        ]

                        if not downloaded_files:
                            continue

                        latest_video_file = max(
                            downloaded_files,
                            key=lambda f: os.path.getctime(os.path.join(temp_dir, f))
                        )

                        video_path = os.path.join(temp_dir, latest_video_file)

                        with open(video_path, "rb") as f:
                            await query.message.reply_video(
                                video=f,
                                caption=f"📹 {story_item.date_utc.strftime('%d-%m-%Y %H:%M')}",
                                filename=latest_video_file,
                                read_timeout=60,
                                write_timeout=60,
                            )
                        
                        if os.path.exists(video_path):
                            os.remove(video_path)

                        sent_count += 1

                    else:
                        loader.download_storyitem(story_item, temp_dir)
                        time.sleep(3)

                        downloaded_files = [
                            f for f in os.listdir(temp_dir)
                            if f.endswith(".jpg")
                        ]

                        if not downloaded_files:
                            continue
                        
                        latest_image_file = max(
                            downloaded_files,
                            key=lambda f: os.path.getctime(os.path.join(temp_dir, f))
                        )

                        image_path = os.path.join(temp_dir, latest_image_file)

                        with open(image_path, "rb") as f:
                            await query.message.reply_photo(
                                photo=f,
                                caption=f"📸 {story_item.date_utc.strftime('%d-%m-%Y %H:%M')}",
                                filename=latest_image_file,
                                read_timeout=60,
                            )
                            
                        if os.path.exists(image_path):
                            os.remove(image_path)

                        sent_count += 1

                    time.sleep(2)

                except Exception as e:
                    logger.error(f"Gagal mengirim story: {str(e)}")
                    continue

            await query.message.reply_text(f"📤 Total {sent_count} story berhasil dikirim")

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info(f"🗑️ Direktori {temp_dir} berhasil dibersihkan")

    except Exception as e:
        logger.error(f"Story error: {str(e)}", exc_info=True)
        await query.message.reply_text("⚠️ Gagal mengambil story")

async def handle_highlights(query, username):
    try:
        profile = Profile.from_username(loader.context, username)
        highlights = list(loader.get_highlights(user=profile))
        
        if not highlights:
            await query.message.reply_text("🌟 Tidak ada highlights yang tersedia")
            return
        
        # Log semua ID yang tersedia
        logger.info(f"Daftar ID Highlight untuk @{username}: {[h.unique_id for h in highlights]}")
        
        # Batasi maksimal 10 highlight
        keyboard = []
        for highlight in highlights[:10]:
            title = highlight.title[:15] + "..." if len(highlight.title) > 15 else highlight.title
            keyboard.append([
                InlineKeyboardButton(
                    f"🌟 {title}",
                    callback_data=f"highlight_{highlight.unique_id}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"Pilih highlight untuk @{username}:",
            reply_markup=reply_markup
        )
    
    except Exception as e:
        logger.error(f"Highlights error: {str(e)}", exc_info=True)
        await query.message.reply_text("⚠️ Gagal mengambil daftar highlight")


async def handle_highlight_items(query, username, highlight_id):
    temp_dir = None  # Inisialisasi variabel di scope terluar
    try:
        profile = Profile.from_username(loader.context, username)
        highlights = list(loader.get_highlights(user=profile))

        # Konversi highlight_id ke integer
        highlight_id_int = int(highlight_id)
        highlight = None
        
        # Cari highlight
        for h in highlights:
            if h.unique_id == highlight_id_int:
                highlight = h
                break

        if not highlight:
            await query.message.reply_text("❌ Highlight tidak ditemukan")
            return

        # Buat direktori temporary
        temp_dir = f"temp_highlight_{username}_{int(time.time())}"
        os.makedirs(temp_dir, exist_ok=True)
        sent_count = 0

        try:
            for item in highlight.get_items():
                # Download item
                loader.download_storyitem(item, target=temp_dir)
                time.sleep(3)

                # Filter file media valid
                valid_extensions = ('.jpg', '.jpeg', '.png', '.mp4', '.mov')
                media_files = [
                    f for f in glob.glob(os.path.join(temp_dir, "*")) 
                    if f.lower().endswith(valid_extensions)
                ]

                if not media_files:
                    logger.warning("Tidak ada file media yang valid")
                    continue

                # Ambil file terbaru
                latest_file = max(media_files, key=os.path.getmtime)

                # Validasi tipe file
                is_video = item.is_video
                expected_ext = ('.mp4', '.mov') if is_video else ('.jpg', '.jpeg', '.png')
                if not latest_file.lower().endswith(expected_ext):
                    logger.error("Ekstensi file tidak sesuai dengan tipe konten")
                    continue

                # Cek ukuran file
                file_size = os.path.getsize(latest_file)
                if file_size > 50 * 1024 * 1024:
                    await query.message.reply_text("⚠️ File melebihi batas 50MB")
                    os.remove(latest_file)
                    continue

                try:
                    with open(latest_file, "rb") as f:
                        if is_video:
                            await query.message.reply_video(
                                video=f,
                                caption=f"🌟 {highlight.title}",
                                read_timeout=60,
                                write_timeout=60
                            )
                        else:
                            await query.message.reply_photo(
                                photo=f,
                                caption=f"🌟 {highlight.title}",
                                read_timeout=60
                            )
                        sent_count += 1
                except Exception as send_error:
                    logger.error(f"Gagal mengirim file: {str(send_error)}")
                finally:
                    if os.path.exists(latest_file):
                        os.remove(latest_file)

                time.sleep(1)

            await query.message.reply_text(f"✅ {sent_count} item dari highlight '{highlight.title}' berhasil dikirim")

        except Exception as e:
            logger.error(f"Error saat memproses item: {str(e)}")
            await query.message.reply_text("⚠️ Gagal memproses item highlight")

        finally:
            # Hapus direktori temporary jika ada
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info(f"Direktori {temp_dir} dihapus")

    except QueryReturnedBadRequestException as e:
        logger.error(f"Error API Instagram: {str(e)}")
        await query.message.reply_text("⚠️ Akses ditolak oleh Instagram")
    except Exception as e:
        logger.error(f"Error highlight: {str(e)}", exc_info=True)
        await query.message.reply_text("⚠️ Gagal memproses highlight")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        

async def handle_profile_info(query, username):
    try:
        profile = Profile.from_username(loader.context, username)
        
        info_text = (
            f"📊 Info Profil @{username}:\n"
            f"👤 Nama: {profile.full_name}\n"
            f"📝 Bio: {profile.biography}\n"
            f"🔗 Followers: {profile.followers:,}\n"
            f"👀 Following: {profile.followees:,}\n"
            f"📌 Post: {profile.mediacount:,}"
        )
        
        await query.message.reply_text(info_text)

    except Exception as e:
        logger.error(f"Profile info error: {str(e)}")
        await query.message.reply_text("⚠️ Gagal mengambil info profil")

def main():
    application = Application.builder().token(env_vars['TOKEN_BOT']).build()
    
    # Tambah handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🤖 Bot started successfully")
    application.run_polling()

if __name__ == "__main__":
    main()
