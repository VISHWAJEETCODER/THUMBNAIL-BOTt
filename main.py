import os
import tempfile
import shutil
import subprocess
import logging
import time
from pathlib import Path
from telegram import Update, Document
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = "7941075102:AAFTMFL4o5GveyguvqSj6By72qZitb9qZbs"
WATERMARK_TEXT = "@RiotFilms"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

WAITING_FOR_VIDEO, WAITING_FOR_THUMBNAIL = range(2)
user_sessions = {}
VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.mkv'}
IMAGE_FORMATS = {'.jpg', '.jpeg', '.png'}
PROGRESS_STEPS = ["⏳", "🔄", "⚡", "🎬", "📤", "✅"]

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleThumbnailBot:
    def __init__(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ultra_bot_"))
        self.active_users = set()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_name = update.effective_user.first_name
        user_id = update.effective_user.id
        welcome_text = (
            f"🎬 **Hi {user_name}!**\n"
            "**Ultra-Light Thumbnail Bot**\n"
            "1️⃣ Send video file (MP4, AVI, MOV, MKV)\n"
            "2️⃣ Send thumbnail image\n"
            "3️⃣ Get video with @RiotFilms thumbnail\n"
            "**Limits:** Video<=100MB, Image<=5MB"
        )
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
        user_sessions[user_id] = {'state': 'started', 'time': time.time()}
        return WAITING_FOR_VIDEO

    async def handle_video_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        document = update.message.document
        if user_id in self.active_users:
            await update.message.reply_text("⚠️ Already processing. Wait or /cancel.")
            return WAITING_FOR_VIDEO
        if not document:
            await update.message.reply_text("❌ Attach a video file.")
            return WAITING_FOR_VIDEO
        file_ext = Path(document.file_name).suffix.lower()
        if file_ext not in VIDEO_FORMATS:
            await update.message.reply_text("❌ Format not supported! MP4, AVI, MOV, MKV only.")
            return WAITING_FOR_VIDEO
        if document.file_size > MAX_FILE_SIZE:
            size_mb = document.file_size / (1024 * 1024)
            await update.message.reply_text(f"❌ Too big! {size_mb:.1f}MB > 100MB. Compress first!")
            return WAITING_FOR_VIDEO
        self.active_users.add(user_id)
        user_sessions[user_id] = {
            'video_file_id': document.file_id,
            'video_file_name': document.file_name,
            'video_file_size': document.file_size,
            'time': time.time()
        }
        await update.message.reply_text(
            f"✅ Video received!\nNow send thumbnail (JPG/PNG, ≤5MB).",
            parse_mode='Markdown'
        )
        return WAITING_FOR_THUMBNAIL

    async def handle_thumbnail_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in user_sessions:
            await update.message.reply_text("❌ Send /start and a video file first!")
            return WAITING_FOR_VIDEO
        file_info = None
        if update.message.photo:
            photo = update.message.photo[-1]
            file_info = {'file_id': photo.file_id, 'file_name': f"thumb_{user_id}.jpg"}
        elif update.message.document:
            document = update.message.document
            file_ext = Path(document.file_name).suffix.lower()
            if file_ext not in IMAGE_FORMATS:
                await update.message.reply_text("❌ Only JPG/PNG images!")
                return WAITING_FOR_THUMBNAIL
            if document.file_size > 5 * 1024 * 1024:
                await update.message.reply_text("❌ Image too large! Max 5MB.")
                return WAITING_FOR_THUMBNAIL
            file_info = {'file_id': document.file_id, 'file_name': document.file_name}
        else:
            await update.message.reply_text("❌ Send an image file!")
            return WAITING_FOR_THUMBNAIL

        user_sessions[user_id].update({
            'thumbnail_file_id': file_info['file_id'],
            'thumbnail_file_name': file_info['file_name']
        })
        await self.process_ultra_light(update, context)
        return ConversationHandler.END

    async def process_ultra_light(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = user_sessions[user_id]
        progress_msg = None
        try:
            timestamp = str(int(time.time()))
            video_name = Path(session['video_file_name'])
            input_video = self.temp_dir / f"v_{user_id}_{timestamp}{video_name.suffix}"
            input_thumb = self.temp_dir / f"t_{user_id}_{timestamp}.jpg"
            watermark_thumb = self.temp_dir / f"w_{user_id}_{timestamp}.jpg"
            output_video = self.temp_dir / f"o_{user_id}_{timestamp}{video_name.suffix}"

            progress_msg = await update.message.reply_text(f"{PROGRESS_STEPS[0]} Starting...")
            await progress_msg.edit_text(f"{PROGRESS_STEPS[1]} Downloading video...")
            video_file = await context.bot.get_file(session['video_file_id'])
            await video_file.download_to_drive(str(input_video))

            await progress_msg.edit_text(f"{PROGRESS_STEPS[2]} Downloading thumbnail...")
            thumb_file = await context.bot.get_file(session['thumbnail_file_id'])
            await thumb_file.download_to_drive(str(input_thumb))

            await progress_msg.edit_text(f"{PROGRESS_STEPS[3]} Adding watermark...")
            if not self.add_simple_watermark(input_thumb, watermark_thumb):
                await progress_msg.edit_text("❌ Watermark failed!")
                return

            await progress_msg.edit_text(f"{PROGRESS_STEPS[4]} Setting thumbnail...")
            if not self.attach_simple_thumbnail(input_video, watermark_thumb, output_video):
                await progress_msg.edit_text("❌ Video processing failed!")
                return

            await progress_msg.edit_text(f"{PROGRESS_STEPS[5]} Uploading...")
            new_filename = f"{video_name.stem}_RiotFilms{video_name.suffix}"
            with open(output_video, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=new_filename,
                    caption=f"✅ Done!\nOriginal: `{session['video_file_name']}`\nModified: `{new_filename}`\nWatermark: `{WATERMARK_TEXT}`",
                    parse_mode='Markdown'
                )
            await progress_msg.edit_text("🎉 Done! Use /start for next file.")

        except Exception as e:
            logger.error(f"Processing error: {e}")
            if progress_msg:
                await progress_msg.edit_text(f"❌ Failed!\nError: {str(e)[:80]}\nTry smaller file or /start again")
        finally:
            self.active_users.discard(user_id)
            if user_id in user_sessions:
                del user_sessions[user_id]
            for file_path in [input_video, input_thumb, watermark_thumb, output_video]:
                try:
                    if file_path.exists():
                        file_path.unlink()
                except:
                    pass

    def add_simple_watermark(self, input_img: Path, output_img: Path) -> bool:
        try:
            with Image.open(input_img) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                font_size = max(18, min(img.width, img.height) // 18)
                font = ImageFont.load_default()
                txt = Image.new('RGBA', img.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(txt)
                text_width, text_height = draw.textbbox((0,0), WATERMARK_TEXT, font=font)[2:]
                x = img.width - text_width - 12
                y = img.height - text_height - 12
                draw.rectangle([x-6, y-6, x+text_width+6, y+text_height+6], fill=(0,0,0,160))
                draw.text((x, y), WATERMARK_TEXT, font=font, fill=(255,255,255,255))
                watermarked = Image.alpha_composite(img.convert('RGBA'), txt)
                watermarked.convert('RGB').save(output_img, 'JPEG', quality=92, optimize=True)
                return True
        except Exception as e:
            logger.error(f"Watermark creation failed: {e}")
            return False

    def attach_simple_thumbnail(self, video_path: Path, thumb_path: Path, output_path: Path) -> bool:
        try:
            cmd = [
                'ffmpeg',
                '-i', str(video_path),
                '-i', str(thumb_path),
                '-map', '0:v:0',
                '-map', '0:a?',
                '-map', '1:0',
                '-c:v:0', 'copy',
                '-c:a', 'copy',
                '-c:v:1', 'mjpeg',
                '-disposition:v:1', 'attached_pic',
                str(output_path),
                '-y'
            ]
            out = subprocess.run(cmd, capture_output=True)
            return out.returncode == 0 and output_path.exists()
        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
            return False

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.active_users.discard(user_id)
        if user_id in user_sessions:
            del user_sessions[user_id]
        await update.message.reply_text("❌ Cancelled! Use /start again.")
        return ConversationHandler.END

def main():
    if not BOT_TOKEN:
        print("❌ Bot token not set.")
        return
    bot = SimpleThumbnailBot()
    app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start_command)],
        states={
            WAITING_FOR_VIDEO: [
                MessageHandler(filters.Document.VIDEO, bot.handle_video_file),
                MessageHandler(~filters.Document.VIDEO, lambda u,c: u.message.reply_text("❌ Attach a video file!"))
            ],
            WAITING_FOR_THUMBNAIL: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, bot.handle_thumbnail_image),
                MessageHandler(~(filters.PHOTO | filters.Document.IMAGE), lambda u,c: u.message.reply_text("❌ Send an image file!"))
            ]
        },
        fallbacks=[
            CommandHandler("cancel", bot.cancel_command),
            CommandHandler("start", bot.start_command)
        ]
    )
    app.add_handler(conv_handler)
    print("✅ Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
