import os
import io
import time
import asyncio
import sqlite3
import logging
import gc
from pathlib import Path

import qrcode
from cryptography.fernet import Fernet
from telethon import TelegramClient, events, Button, errors
from telethon.sessions import StringSession
from telethon.tl import functions


# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_KEY = os.environ["SESSION_ENCRYPTION_KEY"].encode()

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "users.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("selfbot-manager")

fernet = Fernet(SESSION_KEY)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    session TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
""")
db.commit()


def get_user(owner_id):
    return db.execute(
        """SELECT telegram_id, session, user_id, username,
                  first_name, enabled
           FROM users WHERE telegram_id = ?""",
        (owner_id,)
    ).fetchone()


def save_user(owner_id, session_string, me):
    encrypted = fernet.encrypt(session_string.encode()).decode()
    now = int(time.time())

    db.execute("""
        INSERT INTO users
        (telegram_id, session, user_id, username, first_name,
         enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            session=excluded.session,
            user_id=excluded.user_id,
            username=excluded.username,
            first_name=excluded.first_name,
            enabled=1,
            updated_at=excluded.updated_at
    """, (
        owner_id,
        encrypted,
        me.id,
        me.username,
        me.first_name,
        now,
        now
    ))
    db.commit()


def load_session(owner_id):
    row = get_user(owner_id)
    if not row:
        return None

    try:
        return fernet.decrypt(row[1].encode()).decode()
    except Exception:
        log.exception("Could not decrypt session for %s", owner_id)
        return None


def delete_user(owner_id):
    db.execute(
        "DELETE FROM users WHERE telegram_id = ?",
        (owner_id,)
    )
    db.commit()


# ============================================================
# CLIENTS / LOGIN STATE
# ============================================================

bot = TelegramClient("manager_bot", API_ID, API_HASH)

user_clients = {}
login_tasks = {}
password_waiters = {}

DEFAULT_FEATURES = {
    "clock": True,
    "autoseen": True,
    "autoreact": False,
    "afk": False,
}

user_features = {}


def features_for(owner_id):
    if owner_id not in user_features:
        user_features[owner_id] = DEFAULT_FEATURES.copy()
    return user_features[owner_id]


# ============================================================
# USERBOT
# ============================================================

def register_userbot_handlers(client, owner_id):
    features = features_for(owner_id)

    # --------------------------------------------------------
    # .ping
    # --------------------------------------------------------

    @client.on(events.NewMessage(outgoing=True, pattern=r"\.ping$"))
    async def ping(event):
        start = time.perf_counter()
        await event.edit("🏓 Pinging...")
        ms = round((time.perf_counter() - start) * 1000)
        await event.edit(
            f"🏓 <b>Pong!</b>\n⚡ {ms} ms",
            parse_mode="html"
        )

    # --------------------------------------------------------
    # .id
    # --------------------------------------------------------

    @client.on(events.NewMessage(outgoing=True, pattern=r"\.id$"))
    async def my_id(event):
        me = await client.get_me()
        await event.edit(
            f"🆔 <b>Your ID:</b> <code>{me.id}</code>",
            parse_mode="html"
        )

    # --------------------------------------------------------
    # AUTO SEEN
    # --------------------------------------------------------

    @client.on(events.NewMessage(incoming=True))
    async def auto_seen(event):
        if not features.get("autoseen"):
            return
        if not event.is_private:
            return

        try:
            await event.mark_read()
        except Exception:
            pass

    # --------------------------------------------------------
    # CLOCK
    # --------------------------------------------------------

    async def clock_loop():
        while True:
            if features.get("clock"):
                try:
                    # Keep the feature simple: update the last name
                    # with the current time while preserving first name.
                    from datetime import datetime

                    me = await client.get_me()
                    first = me.first_name or ""
                    last = datetime.now().strftime("%H:%M")

                    await client(
                        functions.account.UpdateProfileRequest(
                            first_name=first,
                            last_name=last
                        )
                    )
                except Exception:
                    pass

            await asyncio.sleep(60)

    asyncio.create_task(clock_loop())

    # --------------------------------------------------------
    # AFK
    # --------------------------------------------------------

    afk_state = {
        "active": False,
        "reason": ""
    }

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.afk(?:\s+(.+))?$"
        )
    )
    async def afk(event):
        if not features.get("afk"):
            return

        reason = event.pattern_match.group(1) or "AFK"
        afk_state["active"] = True
        afk_state["reason"] = reason

        await event.edit(
            f"💤 <b>AFK enabled</b>\n{reason}",
            parse_mode="html"
        )

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.back$"
        )
    )
    async def back(event):
        afk_state["active"] = False
        afk_state["reason"] = ""

        await event.edit(
            "🟢 <b>AFK disabled.</b>",
            parse_mode="html"
        )

    @client.on(events.NewMessage(incoming=True))
    async def afk_reply(event):
        if not features.get("afk"):
            return
        if not afk_state["active"]:
            return
        if not event.is_private:
            return

        try:
            await event.reply(
                "💤 I'm AFK right now.\n"
                f"Reason: {afk_state['reason']}"
            )
        except Exception:
            pass

    # --------------------------------------------------------
    # .help
    # --------------------------------------------------------

    @client.on(events.NewMessage(outgoing=True, pattern=r"\.help$"))
    async def help_command(event):
        await event.edit(
            """
<b>USERBOT COMMANDS</b>

<code>.ping</code> — Ping
<code>.id</code> — Your ID
<code>.afk reason</code> — AFK
<code>.back</code> — Disable AFK
<code>.panel</code> — Control panel
<code>.help</code> — Help
""",
            parse_mode="html"
        )

    # --------------------------------------------------------
    # PANEL
    # --------------------------------------------------------

    panel_items = {
        "clock": "🕐 Clock",
        "autoseen": "👀 Auto Seen",
        "autoreact": "❤️ Auto React",
        "afk": "💤 AFK",
    }

    def panel_text():
        lines = [
            "⚙️ <b>USERBOT CONTROL PANEL</b>",
            "━━━━━━━━━━━━━━━━━━",
            ""
        ]

        for key, name in panel_items.items():
            status = "🟢 ON" if features.get(key) else "🔴 OFF"
            lines.append(f"{name} — <b>{status}</b>")

        lines.append("")
        lines.append("Tap a button to toggle.")
        return "\n".join(lines)

    def panel_buttons():
        rows = []
        items = list(panel_items.items())

        for i in range(0, len(items), 2):
            row = []

            for key, name in items[i:i + 2]:
                status = "🟢" if features.get(key) else "🔴"
                row.append(
                    Button.inline(
                        f"{status} {name}",
                        data=f"ub:{key}".encode()
                    )
                )

            rows.append(row)

        rows.append([
            Button.inline("🔄 Refresh", data=b"ub:refresh"),
            Button.inline("❌ Close", data=b"ub:close")
        ])

        return rows

    @client.on(events.NewMessage(outgoing=True, pattern=r"\.panel$"))
    async def panel(event):
        await event.edit(
            panel_text(),
            buttons=panel_buttons(),
            parse_mode="html"
        )

    @client.on(events.CallbackQuery())
    async def panel_callback(event):
        if not event.data or not event.data.startswith(b"ub:"):
            return

        # A panel belongs to the owner of this userbot.
        if event.sender_id != owner_id:
            await event.answer(
                "❌ You cannot control this panel.",
                alert=True
            )
            return

        action = event.data[3:].decode()

        if action == "close":
            await event.edit("⚙️ Panel closed.")
            await event.answer()
            return

        if action == "refresh":
            await event.edit(
                panel_text(),
                buttons=panel_buttons(),
                parse_mode="html"
            )
            await event.answer("Refreshed.")
            return

        if action in features:
            features[action] = not features[action]

            await event.edit(
                panel_text(),
                buttons=panel_buttons(),
                parse_mode="html"
            )

            state = "ON 🟢" if features[action] else "OFF 🔴"
            await event.answer(
                f"{panel_items[action]}: {state}"
            )


async def start_userbot(owner_id, session_string):
    if owner_id in user_clients:
        client = user_clients[owner_id]
        if client.is_connected():
            return client

    client = TelegramClient(
        StringSession(session_string),
        API_ID,
        API_HASH
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        me = await client.get_me()

        user_clients[owner_id] = client
        features_for(owner_id)
        register_userbot_handlers(client, owner_id)

        log.info(
            "Userbot started: owner=%s account=%s",
            owner_id,
            me.id
        )

        return client

    except Exception:
        log.exception("Failed to start userbot %s", owner_id)

        try:
            await client.disconnect()
        except Exception:
            pass

        return None


# ============================================================
# QR + 2FA LOGIN
# ============================================================

async def send_qr(owner_id, chat_id):
    if owner_id in login_tasks:
        await bot.send_message(
            chat_id,
            "⏳ You already have a login process running."
        )
        return

    async def login_worker():
        client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH
        )

        login_tasks[owner_id] = asyncio.current_task()

        try:
            await client.connect()

            qr = await client.qr_login()

            image = qrcode.make(qr.url)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            buffer.name = "telegram_login.png"

            await bot.send_file(
                chat_id,
                buffer,
                caption=(
                    "🔐 <b>Telegram Login</b>\n\n"
                    "Open Telegram → Settings → Devices → "
                    "Link Desktop Device and scan this QR.\n\n"
                    "⌛ QR expires after a short time."
                ),
                parse_mode="html"
            )

            try:
                await qr.wait(timeout=120)

            except errors.SessionPasswordNeededError:
                # Ask for 2FA without saving it anywhere.
                await bot.send_message(
                    chat_id,
                    "🔐 <b>2FA is enabled.</b>\n\n"
                    "Send your Telegram 2FA password in your next message.\n\n"
                    "⚠️ It is used only for this login and is not stored.",
                    parse_mode="html"
                )

                loop = asyncio.get_running_loop()
                future = loop.create_future()
                password_waiters[owner_id] = future

                try:
                    password = await asyncio.wait_for(
                        future,
                        timeout=120
                    )
                except asyncio.TimeoutError:
                    await bot.send_message(
                        chat_id,
                        "⌛ 2FA password input timed out."
                    )
                    return
                finally:
                    password_waiters.pop(owner_id, None)

                try:
                    await client.sign_in(
                        password=password
                    )
                except errors.PasswordHashInvalidError:
                    await bot.send_message(
                        chat_id,
                        "❌ Incorrect 2FA password."
                    )
                    return
                finally:
                    # Remove password references as soon as possible.
                    password = None
                    gc.collect()

            me = await client.get_me()

            session_string = client.session.save()

            save_user(
                owner_id,
                session_string,
                me
            )

            user_clients[owner_id] = client
            features_for(owner_id)
            register_userbot_handlers(
                client,
                owner_id
            )

            await bot.send_message(
                chat_id,
                (
                    "✅ <b>Account connected!</b>\n\n"
                    f"👤 {me.first_name or 'Unknown'}\n"
                    f"🆔 <code>{me.id}</code>\n"
                    f"📛 @{me.username or 'none'}\n\n"
                    "Your personal userbot is now running.\n"
                    "Send <code>/status</code> to check it."
                ),
                parse_mode="html"
            )

            await client.run_until_disconnected()

        except asyncio.CancelledError:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

        except Exception as e:
            log.exception("Login failed for %s", owner_id)

            try:
                await bot.send_message(
                    chat_id,
                    (
                        "❌ <b>Login failed.</b>\n\n"
                        f"<code>{type(e).__name__}</code>"
                    ),
                    parse_mode="html"
                )
            except Exception:
                pass

            try:
                await client.disconnect()
            except Exception:
                pass

        finally:
            login_tasks.pop(owner_id, None)
            password_waiters.pop(owner_id, None)

    asyncio.create_task(login_worker())


# ============================================================
# RECEIVE 2FA PASSWORD
# ============================================================

@bot.on(events.NewMessage())
async def receive_2fa(event):
    owner_id = event.sender_id

    future = password_waiters.get(owner_id)

    if future is None or future.done():
        return

    # Do not interpret bot commands as passwords.
    if event.raw_text.startswith("/"):
        return

    password = event.raw_text

    if password:
        future.set_result(password)

        # Delete the message containing the password.
        try:
            await event.delete()
        except Exception:
            pass


# ============================================================
# BOT COMMANDS
# ============================================================

@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_command(event):
    row = get_user(event.sender_id)

    if row:
        await event.reply(
            """
🤖 <b>Your Userbot</b>

Your Telegram account is already connected.

<code>/status</code> — Status
<code>/stop</code> — Stop
<code>/logout</code> — Disconnect
""",
            parse_mode="html"
        )
        return

    await event.reply(
        """
🤖 <b>Personal Userbot</b>

Connect your own Telegram account using QR Login.

🔐 Your account is authorized directly through Telegram.

Send <code>/login</code> to begin.
""",
        parse_mode="html"
    )


@bot.on(events.NewMessage(pattern=r"^/login$"))
async def login_command(event):
    owner_id = event.sender_id

    if owner_id in login_tasks:
        await event.reply(
            "⏳ A login is already in progress."
        )
        return

    if owner_id in user_clients:
        await event.reply(
            "✅ Your account is already connected."
        )
        return

    await event.reply(
        "🔐 Generating your personal QR code..."
    )

    await send_qr(
        owner_id,
        event.chat_id
    )


@bot.on(events.NewMessage(pattern=r"^/status$"))
async def status_command(event):
    owner_id = event.sender_id
    client = user_clients.get(owner_id)

    if not client:
        session = load_session(owner_id)

        if session:
            client = await start_userbot(
                owner_id,
                session
            )

    if not client:
        await event.reply(
            "🔴 No running userbot.\n\nUse /login"
        )
        return

    try:
        me = await client.get_me()

        await event.reply(
            (
                "🟢 <b>Userbot Online</b>\n\n"
                f"👤 {me.first_name or ''}\n"
                f"🆔 <code>{me.id}</code>\n"
                f"📛 @{me.username or 'none'}"
            ),
            parse_mode="html"
        )

    except Exception:
        await event.reply(
            "🔴 Userbot is offline."
        )


@bot.on(events.NewMessage(pattern=r"^/stop$"))
async def stop_command(event):
    owner_id = event.sender_id
    client = user_clients.get(owner_id)

    if not client:
        await event.reply(
            "🔴 Your userbot isn't running."
        )
        return

    try:
        await client.disconnect()
    except Exception:
        pass

    user_clients.pop(owner_id, None)

    await event.reply(
        "⏹ <b>Your userbot has been stopped.</b>",
        parse_mode="html"
    )


@bot.on(events.NewMessage(pattern=r"^/logout$"))
async def logout_command(event):
    owner_id = event.sender_id

    client = user_clients.get(owner_id)

    if client:
        try:
            await client.log_out()
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass

        user_clients.pop(owner_id, None)

    delete_user(owner_id)
    user_features.pop(owner_id, None)

    await event.reply(
        """
🗑 <b>Account disconnected.</b>

The stored session has been removed.

Use /login to connect again.
""",
        parse_mode="html"
    )


# ============================================================
# RESTORE SAVED USERBOTS
# ============================================================

async def load_existing_users():
    rows = db.execute(
        "SELECT telegram_id FROM users WHERE enabled = 1"
    ).fetchall()

    for (owner_id,) in rows:
        session = load_session(owner_id)

        if not session:
            continue

        try:
            await start_userbot(
                owner_id,
                session
            )
        except Exception:
            log.exception(
                "Could not restore userbot %s",
                owner_id
            )


# ============================================================
# MAIN
# ============================================================

async def main():
    await bot.start(bot_token=BOT_TOKEN)

    log.info("Manager bot started.")

    await load_existing_users()

    log.info("Existing userbots restored.")

    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
