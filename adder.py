import os, json, asyncio
from telethon import TelegramClient, events, Button, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import User, Channel

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DELAY = int(os.getenv("DELAY", "10"))          # ثانیه بین هر نفر
DATA_DIR = os.getenv("DATA_DIR", ".")
SESSION_FILE = os.path.join(DATA_DIR, "session.txt")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

bot = TelegramClient(StringSession(), API_ID, API_HASH)
user = None          # کلاینت حساب شخصی
login = {"step": None, "phone": "", "hash": "", "code": ""}
running = False
task = None


class Stop(Exception):
    pass


# ---------- ذخیره‌ی ساده در فایل ----------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"target": "ParsaSelfBot", "done": [], "errors": [],
                "stats": {"checked": 0, "added": 0, "already": 0, "errors": 0}}


state = load_state()


def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def log_err(msg):
    state["stats"]["errors"] += 1
    state["errors"] = (state["errors"] + [msg])[-30:]


# ---------- دکمه‌ها ----------
def menu():
    return [
        [Button.inline("🔐 وضعیت حساب", b"st"), Button.inline("👥 بررسی گروه‌ها", b"gr")],
        [Button.inline("🏘️ گروه مقصد", b"tg"), Button.inline("📊 آمار", b"stat")],
        [Button.inline("▶️ شروع", b"run"), Button.inline("⏹ توقف", b"stop")],
        [Button.inline("⚠️ خطاها", b"err"), Button.inline("🔄 بررسی مجدد", b"re")],
        [Button.inline("🚪 خروج از حساب", b"logout")],
    ]


def keypad():
    rows = [[Button.inline(str(n), f"d{n}".encode()) for n in r]
            for r in ((1, 2, 3), (4, 5, 6), (7, 8, 9))]
    rows.append([Button.inline("⌫", b"dx"), Button.inline("0", b"d0"),
                 Button.inline("✅ تأیید", b"ok")])
    return rows


def code_text():
    c = login["code"]
    shown = " ".join(c) if c else "—"
    return f"🔢 کد تأییدی که تلگرام فرستاد رو با دکمه‌ها وارد کن:\n\nکد: {shown}"


async def authorized():
    return user is not None and await user.is_user_authorized()


async def panel_text():
    if not await authorized():
        return "🤖 پنل مدیریت سلف‌بات\n\n❌ حساب وصل نیست."
    me = await user.get_me()
    state_txt = "▶️ در حال اجرا" if running else "⏹ متوقف"
    return (f"🤖 پنل مدیریت سلف‌بات\n\n✅ حساب: {me.first_name} "
            f"(@{me.username or '-'})\n🏘️ مقصد: @{state['target']}\n"
            f"وضعیت: {state_txt}")


async def show_panel(event, edit=False):
    if await authorized():
        txt, btns = await panel_text(), menu()
    else:
        txt, btns = await panel_text(), [[Button.inline("🔐 ورود به حساب", b"login")]]
    if edit:
        try:
            await event.edit(txt, buttons=btns)
        except Exception:
            pass
    else:
        await event.respond(txt, buttons=btns)


async def new_client(session=""):
    global user
    user = TelegramClient(StringSession(session), API_ID, API_HASH)
    await user.connect()


async def finish_login(event):
    with open(SESSION_FILE, "w") as f:
        f.write(user.session.save())
    login.update(step=None, phone="", hash="", code="")
    await event.respond("✅ وارد شدی.")
    await show_panel(event)


# ---------- پیام‌های متنی ----------
@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.sender_id == OWNER_ID))
async def on_message(event):
    text = (event.raw_text or "").strip()
    step = login["step"]

    if text == "/start":
        return await show_panel(event)

    if step == "phone":
        try:
            await new_client()
            sent = await user.send_code_request(text)
        except errors.PhoneNumberInvalidError:
            return await event.respond("❌ شماره درست نیست. مثل +989121234567 بفرست.")
        except errors.FloodWaitError as e:
            return await event.respond(f"⏳ {e.seconds} ثانیه صبر کن و دوباره امتحان کن.")
        except Exception as e:
            return await event.respond(f"❌ خطا: {e}")
        login.update(step="code", phone=text, hash=sent.phone_code_hash, code="")
        return await event.respond(code_text(), buttons=keypad())

    if step == "password":
        try:
            await event.delete()
        except Exception:
            pass
        try:
            await user.sign_in(password=text)
        except errors.PasswordHashInvalidError:
            return await event.respond("❌ رمز اشتباهه، دوباره بفرست.")
        return await finish_login(event)

    if step == "target":
        name = text.replace("@", "").replace("https://t.me/", "").strip()
        try:
            await user.get_entity(name)
        except Exception:
            return await event.respond("❌ پیدا نشد. یوزرنیم گروه رو دوباره بفرست.")
        state["target"] = name
        save_state()
        login["step"] = None
        await event.respond(f"✅ گروه مقصد: @{name}")
        return await show_panel(event)


# ---------- دکمه‌ها ----------
@bot.on(events.CallbackQuery(func=lambda e: e.sender_id == OWNER_ID))
async def on_button(event):
    global running, task, user
    d = event.data.decode()
    await event.answer()

    if d == "login":
        login.update(step="phone", code="")
        return await event.edit("📱 شمارهٔ حساب رو با کد کشور بفرست.\nمثل: +989121234567")

    # کیبورد شیشه‌ای کد
    if login["step"] == "code" and (d.startswith("d") or d == "ok"):
        if d == "dx":
            login["code"] = login["code"][:-1]
        elif d == "ok":
            if not login["code"]:
                return
            try:
                await user.sign_in(login["phone"], login["code"], phone_code_hash=login["hash"])
            except errors.SessionPasswordNeededError:
                login["step"] = "password"
                return await event.edit("🔑 رمز تأیید دومرحله‌ای رو بفرست:")
            except errors.PhoneCodeInvalidError:
                login["code"] = ""
                return await event.edit("❌ کد اشتباه بود.\n\n" + code_text(), buttons=keypad())
            except errors.PhoneCodeExpiredError:
                login["step"] = None
                return await event.edit("⌛ کد منقضی شد. دوباره وارد شو.",
                                        buttons=[[Button.inline("🔐 ورود به حساب", b"login")]])
            await event.edit("✅ کد تأیید شد.")
            return await finish_login(event)
        elif len(login["code"]) < 6:
            login["code"] += d[1:]
        try:
            await event.edit(code_text(), buttons=keypad())
        except Exception:
            pass
        return

    if not await authorized():
        return await show_panel(event, edit=True)

    if d in ("st", "re"):
        await show_panel(event, edit=True)

    elif d == "gr":
        names = []
        async for dlg in user.iter_dialogs():
            if dlg.is_group:
                names.append(dlg.title)
        txt = f"👥 {len(names)} گروه در دسترس\n\n" + "\n".join("• " + n for n in names[:15])
        await event.respond(txt)

    elif d == "tg":
        login["step"] = "target"
        await event.respond(f"🏘️ مقصد فعلی: @{state['target']}\nیوزرنیم گروه جدید رو بفرست (یا /start برای انصراف).")

    elif d == "stat":
        s = state["stats"]
        await event.respond(f"📊 آمار\n\nبررسی‌شده: {s['checked']}\nاضافه‌شده: {s['added']}\n"
                            f"از قبل عضو: {s['already']}\nخطا: {s['errors']}")

    elif d == "err":
        e = state["errors"][-10:]
        await event.respond("⚠️ آخرین خطاها\n\n" + ("\n".join(e) if e else "خطایی نیست."))

    elif d == "run":
        if running:
            return await event.respond("از قبل در حال اجراست.")
        task = asyncio.create_task(worker())
        await event.respond("▶️ شروع شد. هر %d ثانیه یک نفر." % DELAY)

    elif d == "stop":
        running = False
        await event.respond("⏹ دستور توقف داده شد.")

    elif d == "logout":
        running = False
        try:
            await user.log_out()
        except Exception:
            pass
        user = None
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        await event.respond("🚪 خارج شدی.")
        await show_panel(event)


# ---------- کار اصلی ----------
async def add_one(target, u):
    if not u.contact:
        try:
            await user(functions.contacts.AddContactRequest(
                id=u, first_name=u.first_name or "-", last_name=u.last_name or "",
                phone="", add_phone_privacy_exception=False))
        except errors.FloodWaitError:
            raise
        except Exception:
            pass  # نشد مخاطب بشه، مهم نیست
    if isinstance(target, Channel):
        res = await user(functions.channels.InviteToChannelRequest(target, [u]))
        if getattr(res, "missing_invitees", None):
            raise errors.UserPrivacyRestrictedError(None)
    else:
        await user(functions.messages.AddChatUserRequest(target.id, u, fwd_limit=0))


async def worker():
    global running
    running = True
    done = set(state["done"])
    try:
        target = await user.get_entity(state["target"])
        have = set()
        async for p in user.iter_participants(target):
            have.add(p.id)

        async for dlg in user.iter_dialogs():
            if not running:
                break
            if not dlg.is_group or dlg.entity.id == target.id:
                continue
            try:
                async for u in user.iter_participants(dlg.entity):
                    if not running:
                        break
                    if not isinstance(u, User) or u.bot or u.deleted or u.is_self or u.id in done:
                        continue
                    state["stats"]["checked"] += 1
                    if u.id in have:
                        state["stats"]["already"] += 1
                        done.add(u.id)
                        continue
                    try:
                        await add_one(target, u)
                        state["stats"]["added"] += 1
                        have.add(u.id)
                    except errors.FloodWaitError as e:
                        log_err(f"FloodWait {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                        continue  # ثبت نمی‌شه تا بعداً دوباره امتحان بشه
                    except errors.PeerFloodError:
                        log_err("PeerFlood: تلگرام محدودیت اضافه‌کردن گذاشته")
                        await bot.send_message(OWNER_ID, "🛑 تلگرام محدودیت گذاشت (PeerFlood). متوقف شد.")
                        raise Stop()
                    except (errors.ChatAdminRequiredError, errors.ChatWriteForbiddenError):
                        log_err("اجازهٔ اضافه‌کردن توی گروه مقصد نداری")
                        await bot.send_message(OWNER_ID, "🛑 توی گروه مقصد اجازهٔ اضافه‌کردن نداری. متوقف شد.")
                        raise Stop()
                    except errors.UserAlreadyParticipantError:
                        state["stats"]["already"] += 1
                    except Exception as e:
                        log_err(f"{u.id}: {type(e).__name__}")
                    done.add(u.id)
                    state["done"] = list(done)
                    save_state()
                    await asyncio.sleep(DELAY)
            except Stop:
                raise
            except Exception as e:
                log_err(f"گروه {dlg.title}: {type(e).__name__}")
        await bot.send_message(OWNER_ID, "✅ بررسی همهٔ گروه‌ها تموم شد.")
    except Stop:
        pass
    except Exception as e:
        log_err(f"worker: {type(e).__name__}: {e}")
        await bot.send_message(OWNER_ID, f"❌ خطا: {e}")
    finally:
        running = False
        state["done"] = list(done)
        save_state()


# ---------- سرور سلامت برای Render ----------
async def health():
    async def h(r, w):
        await r.read(1024)
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await w.drain()
        w.close()
    await asyncio.start_server(h, "0.0.0.0", int(os.getenv("PORT", "10000")))


async def main():
    await health()
    await bot.start(bot_token=BOT_TOKEN)
    s = os.getenv("SESSION", "")
    if not s and os.path.exists(SESSION_FILE):
        s = open(SESSION_FILE).read().strip()
    if s:
        await new_client(s)
        if not await user.is_user_authorized():
            await user.disconnect()
            globals()["user"] = None
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
