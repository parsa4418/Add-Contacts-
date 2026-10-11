import os
import sqlite3
import asyncio
import threading
import logging
from concurrent.futures import run_coroutine_threadsafe

from flask import Flask, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("contact-manager")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
TARGET_GROUP = os.environ.get("TARGET_GROUP", "ParsaSelfBot").lstrip("@")
DB_PATH = os.environ.get("DB_PATH", "/tmp/contact_manager.sqlite3")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
user_loop = asyncio.new_event_loop()
loop_ready = threading.Event()
scan_lock = threading.Lock()
scan_running = False

def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS candidates (
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

def set_status(user_id, status, error=""):
    with db() as con:
        con.execute("UPDATE candidates SET status=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                    (status, error[:500], user_id))

def get_candidate(user_id):
    with db() as con:
        return con.execute("SELECT * FROM candidates WHERE user_id=?", (user_id,)).fetchone()

def is_admin_message(message):
    return message.from_user and message.from_user.id == ADMIN_USER_ID

def is_admin_call(call):
    return call.from_user and call.from_user.id == ADMIN_USER_ID

def main_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔎 اسکن چت‌های مستقیم", callback_data="scan"))
    kb.row(InlineKeyboardButton("📋 افراد منتظر بررسی", callback_data="pending"),
           InlineKeyboardButton("🚫 فهرست‌های ردشده/ناموفق", callback_data="skipped"))
    return kb

@app.get("/")
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "telegram-contact-manager"}), 200

async def scan_account():
    contacts = await client.get_contacts()
    contact_ids = {u.id for u in contacts if isinstance(u, User)}
    added = 0
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, User) or getattr(entity, "bot", False) or getattr(entity, "deleted", False):
            continue
        if entity.id in contact_ids:
            continue
        existing = get_candidate(entity.id)
        # Do not re-process anyone already approved, rejected, added, or failed.
        if existing and existing["status"] != "pending":
            continue
        name = " ".join(filter(None, [entity.first_name, entity.last_name])) or "بدون نام"
        with db() as con:
            con.execute("""INSERT INTO candidates(user_id,name,username,status)
                           VALUES(?,?,?,'pending')
                           ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username
                           WHERE candidates.status='pending'""",
                        (entity.id, name, entity.username or ""))
        added += 1
    return added

def submit_user_coro(coro):
    if not loop_ready.wait(timeout=15):
        raise RuntimeError("اکانت تلگرام هنوز آماده نیست.")
    return run_coroutine_threadsafe(coro, user_loop).result(timeout=180)

def send_candidate(chat_id, item):
    username = f"@{item['username']}" if item["username"] else "ندارد"
    text = (f"👤 <b>{item['name']}</b>\n"
            f"🆔 آیدی عددی: <code>{item['user_id']}</code>\n"
            f"🔗 نام کاربری: {username}\n\n"
            "آیا این شخص مشخصاً برای اضافه‌شدن به گروه رضایت داده؟")
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("✅ رضایت دارد؛ تلاش برای افزودن", callback_data=f"yes:{item['user_id']}"))
    kb.row(InlineKeyboardButton("❌ رضایت ندارد", callback_data=f"no:{item['user_id']}"))
    bot.send_message(chat_id, text, reply_markup=kb)

def send_next_pending(chat_id):
    with db() as con:
        item = con.execute("SELECT * FROM candidates WHERE status='pending' ORDER BY user_id LIMIT 1").fetchone()
    if not item:
        bot.send_message(chat_id, "✅ مورد دیگری برای بررسی باقی نمانده.", reply_markup=main_keyboard())
        return
    send_candidate(chat_id, item)

async def add_to_target(user_id):
    target = await client.get_entity(TARGET_GROUP)
    user = await client.get_entity(user_id)
    # Supports public supergroups/channels and legacy basic groups.
    if getattr(target, "megagroup", False) or getattr(target, "broadcast", False) or target.__class__.__name__ == "Channel":
        await client(InviteToChannelRequest(target, [user]))
    else:
        await client(AddChatUserRequest(chat_id=target.id, user_id=user, fwd_limit=0))

@bot.message_handler(commands=["start", "help"])
def start(message):
    if not is_admin_message(message):
        return
    bot.send_message(
        message.chat.id,
        "👋 <b>مدیر بررسی و دعوت مخاطبین</b>\n\n"
        f"گروه مقصد: <code>@{TARGET_GROUP}</code>\n"
        "فقط چت‌های مستقیم بررسی می‌شوند؛ اعضای گروه‌ها اسکن نمی‌شوند. "
        "هر نفر پیش از تلاش برای افزودن، نیاز به تأیید تو دارد. "
        "افراد ردشده یا دارای خطا دوباره امتحان نمی‌شوند.\n\n"
        "نکته: فایل SQLite روی فضای موقت Render ممکن است با ری‌استارت/دیپلوی پاک شود؛ "
        "برای نگهداری دائمی باید دیسک پایدار یا دیتابیس خارجی تنظیم شود.",
        reply_markup=main_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data in ("scan", "pending", "skipped") or c.data.startswith(("yes:", "no:")))
def callbacks(call):
    if not is_admin_call(call):
        bot.answer_callback_query(call.id, "اجازه دسترسی نداری.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    if call.data == "scan":
        def worker():
            global scan_running
            if not scan_lock.acquire(blocking=False):
                bot.send_message(chat_id, "⏳ اسکن دیگری در حال اجراست.")
                return
            scan_running = True
            try:
                bot.send_message(chat_id, "🔎 اسکن چت‌های مستقیم شروع شد…")
                count = submit_user_coro(scan_account())
                bot.send_message(chat_id, f"✅ اسکن تمام شد؛ {count} مورد منتظر بررسی است.", reply_markup=main_keyboard())
            except Exception:
                log.exception("scan failed")
                bot.send_message(chat_id, "❌ اسکن انجام نشد. تنظیمات نشست اکانت و گزارش Render را بررسی کن.")
            finally:
                scan_running = False
                scan_lock.release()
        threading.Thread(target=worker, daemon=True).start()
        return
    if call.data == "pending":
        send_next_pending(chat_id)
        return
    if call.data == "skipped":
        with db() as con:
            rows = con.execute("SELECT user_id,name,status,error FROM candidates WHERE status IN ('rejected','failed','added','already_member') ORDER BY updated_at DESC LIMIT 30").fetchall()
        if not rows:
            bot.send_message(chat_id, "فهرست ردشده‌ها/ناموفق‌ها خالی است.")
        else:
            lines = ["🚫 <b>ردشده‌ها و مواردی که دیگر امتحان نمی‌شوند</b>"]
            for r in rows:
                err = f" — {r['error']}" if r['error'] else ""
                lines.append(f"• {r['name']} — <code>{r['user_id']}</code> — {r['status']}{err}")
            bot.send_message(chat_id, "\n".join(lines))
        return

    action, raw_id = call.data.split(":", 1)
    user_id = int(raw_id)
    item = get_candidate(user_id)
    if not item or item["status"] != "pending":
        bot.send_message(chat_id, "این مورد قبلاً بررسی شده یا دیگر در انتظار نیست.")
        return
    if action == "no":
        set_status(user_id, "rejected")
        bot.send_message(chat_id, f"⏭️ {item['name']} رد شد و دوباره بررسی نمی‌شود.")
        send_next_pending(chat_id)
        return

    # "yes" means the admin confirms this specific person's consent.
    set_status(user_id, "approved")
    def add_worker():
        try:
            submit_user_coro(add_to_target(user_id))
            set_status(user_id, "added")
            bot.send_message(chat_id, f"✅ تلاش برای افزودن {item['name']} موفق بود.")
        except UserAlreadyParticipantError:
            set_status(user_id, "already_member")
            bot.send_message(chat_id, f"ℹ️ {item['name']} از قبل عضو گروه است؛ دوباره امتحان نمی‌شود.")
        except FloodWaitError as e:
            set_status(user_id, "failed", f"FloodWait: {e.seconds}s")
            bot.send_message(chat_id, f"⚠️ افزودن {item['name']} انجام نشد (محدودیت تلگرام)؛ ثبت شد و دوباره امتحان نمی‌شود.")
        except Exception as e:
            log.exception("add failed for %s", user_id)
            set_status(user_id, "failed", f"{type(e).__name__}: {e}")
            bot.send_message(chat_id, f"⚠️ افزودن {item['name']} ناموفق بود؛ در فهرست «دیگر امتحان نشوند» ثبت شد.")
        finally:
            send_next_pending(chat_id)
    threading.Thread(target=add_worker, daemon=True).start()

def user_client_thread():
    asyncio.set_event_loop(user_loop)
    async def run_client():
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("SESSION_STRING مجاز نیست؛ نشست را دوباره بساز.")
        me = await client.get_me()
        log.info("User session ready; account id=%s", me.id)
        loop_ready.set()
        await client.run_until_disconnected()
    user_loop.create_task(run_client())
    user_loop.run_forever()

def start_bot():
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=user_client_thread, daemon=True).start()
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
