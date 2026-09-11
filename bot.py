import os
import io
import time
import asyncio
import sqlite3
import logging
import secrets
from pathlib import Path

import qrcode
from cryptography.fernet import Fernet

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon import errors


# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Generate once:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    session TEXT,
    user_id INTEGER,
    username TEXT,
    first_name TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER,
    updated_at INTEGER
)
""")

db.commit()


def get_user(telegram_id):
    return db.execute(
        """
        SELECT telegram_id, session, user_id,
               username, first_name, enabled
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()


def save_user(
    telegram_id,
    session,
    user_id,
    username,
    first_name
):
    encrypted = fernet.encrypt(
        session.encode()
    ).decode()

    now = int(time.time())

    db.execute(
        """
        INSERT INTO users (
            telegram_id,
            session,
            user_id,
            username,
            first_name,
            enabled,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            session = excluded.session,
            user_id = excluded.user_id,
            username = excluded.username,
            first_name = excluded.first_name,
            enabled = 1,
            updated_at = excluded.updated_at
        """,
        (
            telegram_id,
            encrypted,
            user_id,
            username,
            first_name,
            now,
            now
        )
    )

    db.commit()


def load_session(telegram_id):
    row = get_user(telegram_id)

    if not row or not row[1]:
        return None

    try:
        return fernet.decrypt(
            row[1].encode()
        ).decode()

    except Exception:
        log.exception(
            "Could not decrypt session for %s",
            telegram_id
        )

        return None


def delete_user(telegram_id):
    db.execute(
        "DELETE FROM users WHERE telegram_id = ?",
        (telegram_id,)
    )

    db.commit()


# ============================================================
# BOT
# ============================================================

bot = TelegramClient(
    "manager_bot",
    API_ID,
    API_HASH
)


# ============================================================
# RUNNING USERBOTS
# ============================================================

user_clients = {}

login_tasks = {}


# ============================================================
# USERBOT FEATURES
# ============================================================

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
# USERBOT START
# ============================================================

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

            log.warning(
                "Session expired for %s",
                owner_id
            )

            return None

        me = await client.get_me()

        user_clients[owner_id] = client

        features_for(owner_id)

        register_userbot_handlers(
            client,
            owner_id
        )

        log.info(
            "Userbot started: %s (%s)",
            me.id,
            me.username
        )

        return client

    except Exception:

        log.exception(
            "Failed to start userbot %s",
            owner_id
        )

        try:
            await client.disconnect()
        except Exception:
            pass

        return None


# ============================================================
# USERBOT HANDLERS
# ============================================================

def register_userbot_handlers(client, owner_id):

    features = features_for(owner_id)

    # --------------------------------------------------------
    # PING
    # --------------------------------------------------------

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.ping$"
        )
    )
    async def ping(event):

        start = time.perf_counter()

        msg = await event.edit(
            "🏓 Pinging..."
        )

        ms = round(
            (time.perf_counter() - start) * 1000
        )

        await msg.edit(
            f"🏓 <b>Pong!</b>\n"
            f"⚡ {ms} ms",
            parse_mode="html"
        )

    # --------------------------------------------------------
    # ID
    # --------------------------------------------------------

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.id$"
        )
    )
    async def my_id(event):

        me = await client.get_me()

        await event.edit(
            f"🆔 <b>Your ID:</b> <code>{me.id}</code>",
            parse_mode="html"
        )

    # --------------------------------------------------------
    # AUTO SEEN
    # --------------------------------------------------------

    @client.on(
        events.NewMessage(incoming=True)
    )
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
                    from datetime import datetime

                    now = datetime.now().strftime(
                        "%H:%M"
                    )

                    me = await client.get_me()

                    await client(
                        __import__(
                            "telethon"
                        ).functions.account.UpdateProfileRequest(
                            first_name=(
                                me.first_name or ""
                            ),
                            last_name=(
                                me.last_name or ""
                            ),
                        )
                    )

                except Exception:
                    pass

            await asyncio.sleep(60)

    asyncio.create_task(
        clock_loop()
    )

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

        reason = event.pattern_match.group(1)

        afk_state["active"] = True
        afk_state["reason"] = (
            reason or "AFK"
        )

        await event.edit(
            f"💤 <b>AFK enabled</b>\n"
            f"{afk_state['reason']}",
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

    @client.on(
        events.NewMessage(incoming=True)
    )
    async def afk_reply(event):

        if not features.get("afk"):
            return

        if not afk_state["active"]:
            return

        if not event.is_private:
            return

        try:

            await event.reply(
                f"💤 I'm AFK right now.\n"
                f"Reason: {afk_state['reason']}"
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.help$"
        )
    )
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

            status = (
                "🟢 ON"
                if features.get(key)
                else "🔴 OFF"
            )

            lines.append(
                f"{name} — <b>{status}</b>"
            )

        lines += [
            "",
            "Tap a button to toggle."
        ]

        return "\n".join(lines)

    def panel_buttons():

        from telethon import Button

        buttons = []

        items = list(panel_items.items())

        for i in range(
            0,
            len(items),
            2
        ):

            row = []

            for key, name in items[i:i + 2]:

                status = (
                    "🟢"
                    if features.get(key)
                    else "🔴"
                )

                row.append(
                    Button.inline(
                        f"{status} {name}",
                        data=(
                            f"ub:{key}"
                        ).encode()
                    )
                )

            buttons.append(row)

        buttons.append([
            Button.inline(
                "🔄 Refresh",
                data=b"ub:refresh"
            ),
            Button.inline(
                "❌ Close",
                data=b"ub:close"
            )
        ])

        return buttons

    @client.on(
        events.NewMessage(
            outgoing=True,
            pattern=r"\.panel$"
        )
    )
    async def panel(event):

        await event.edit(
            panel_text(),
            buttons=panel_buttons(),
            parse_mode="html"
        )

    @client.on(
        events.CallbackQuery()
    )
    async def panel_callback(event):

        if not event.data:
            return

        if not event.data.startswith(
            b"ub:"
        ):
            return

        # فقط صاحب همین userbot
        if event.sender_id != owner_id:

            await event.answer(
                "❌ You cannot control this panel.",
                alert=True
            )

            return

        action = event.data[
            3:
        ].decode()

        if action == "close":

            await event.edit(
                "⚙️ Panel closed."
            )

            await event.answer()

            return

        if action == "refresh":

            await event.edit(
                panel_text(),
                buttons=panel_buttons(),
                parse_mode="html"
            )

            await event.answer(
                "Refreshed."
            )

            return

        if action in features:

            features[action] = not features[action]

            status = (
                "ON 🟢"
                if features[action]
                else "OFF 🔴"
            )

            await event.edit(
                panel_text(),
                buttons=panel_buttons(),
                parse_mode="html"
            )

            await event.answer(
                f"{panel_items.get(action, action)}: {status}"
            )


# ============================================================
# QR LOGIN
# ============================================================

async def create_qr_login(
    owner_id,
    chat_id
):

    # Don't allow multiple login processes
    if owner_id in login_tasks:

        await bot.send_message(
            chat_id,
            "⏳ You already have a login process running."
        )

        return

    async def login():

        client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH
        )

        try:

            await client.connect()

            if await client.is_user_authorized():

                await bot.send_message(
                    chat_id,
                    "✅ This login session is already authorized."
                )

                await client.disconnect()

                return

            qr = await client.qr_login()

            # ------------------------------------------------
            # Generate QR image
            # ------------------------------------------------

            qr_image = qrcode.make(
                qr.url
            )

            image_buffer = io.BytesIO()

            qr_image.save(
                image_buffer,
                format="PNG"
            )

            image_buffer.seek(0)

            image_buffer.name = "telegram_login.png"

            await bot.send_file(
                chat_id,
                image_buffer,
                caption=(
                    "🔐 <b>Telegram Login</b>\n\n"
                    "1. Open Telegram\n"
                    "2. Settings → Devices\n"
                    "3. Link Desktop Device\n"
                    "4. Scan this QR code\n\n"
                    "⏳ The QR code expires automatically."
                ),
                parse_mode="html"
            )

            # IMPORTANT:
            # wait() must already be running while
            # the QR is being scanned.

            try:

                await qr.wait(
                    timeout=120
                )

            except errors.SessionPasswordNeededError:

                await bot.send_message(
                    chat_id,
                    "🔐 This account has 2FA enabled.\n\n"
                    "For security, this bot does not collect or store your Telegram 2FA password.\n"
                    "Use the normal Telegram login flow instead."
                )

                await client.disconnect()

                return

            except asyncio.TimeoutError:

                await bot.send_message(
                    chat_id,
                    "⌛ QR code expired.\n"
                    "Send /login to generate a new one."
                )

                await client.disconnect()

                return

            # ------------------------------------------------
            # Login successful
            # ------------------------------------------------

            me = await client.get_me()

            session_string = client.session.save()

            save_user(
                telegram_id=owner_id,
                session=session_string,
                user_id=me.id,
                username=me.username,
                first_name=me.first_name
            )

            # Start userbot using same client
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
                    "Your personal userbot is now running.\n\n"
                    "Use <code>/status</code> to check it."
                ),
                parse_mode="html"
            )

            log.info(
                "Account %s connected for bot user %s",
                me.id,
                owner_id
            )

            # Keep this client alive
            await client.run_until_disconnected()

        except Exception as e:

            log.exception(
                "QR login failed"
            )

            await bot.send_message(
                chat_id,
                (
                    "❌ Login failed.\n\n"
                    f"<code>{type(e).__name__}</code>"
                ),
                parse_mode="html"
            )

            try:
                await client.disconnect()
            except Exception:
                pass

        finally:

            login_tasks.pop(
                owner_id,
                None
            )

    task = asyncio.create_task(
        login()
    )

    login_tasks[owner_id] = task


# ============================================================
# BOT COMMANDS
# ============================================================

@bot.on(
    events.NewMessage(
        pattern=r"^/start$"
    )
)
async def start_command(event):

    user_id = event.sender_id

    row = get_user(user_id)

    if row:

        await event.reply(
            """
🤖 <b>Your Userbot</b>

Your Telegram account is already connected.

<b>Commands:</b>

<code>/status</code> — Status
<code>/login</code> — Connect account
<code>/stop</code> — Stop userbot
<code>/logout</code> — Disconnect account
""",
            parse_mode="html"
        )

        return

    await event.reply(
        """
🤖 <b>Personal Userbot</b>

Connect your own Telegram account and run your personal Telethon userbot.

🔐 Login is performed using Telegram's QR login.

Your session is isolated from other users.

Send:

<code>/login</code>
""",
        parse_mode="html"
    )


@bot.on(
    events.NewMessage(
        pattern=r"^/login$"
    )
)
async def login_command(event):

    user_id = event.sender_id

    if user_id in login_tasks:

        await event.reply(
            "⏳ A login is already in progress."
        )

        return

    if user_id in user_clients:

        await event.reply(
            "✅ Your account is already connected."
        )

        return

    await event.reply(
        "🔐 Generating your personal QR code..."
    )

    await create_qr_login(
        user_id,
        event.chat_id
    )


@bot.on(
    events.NewMessage(
        pattern=r"^/status$"
    )
)
async def status_command(event):

    user_id = event.sender_id

    client = user_clients.get(
        user_id
    )

    if not client:

        row = get_user(user_id)

        if not row:

            await event.reply(
                "🔴 No account connected.\n\n"
                "Use /login"
            )

            return

        session = load_session(
            user_id
        )

        if session:

            client = await start_userbot(
                user_id,
                session
            )

    if not client:

        await event.reply(
            "🔴 Userbot is offline."
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


@bot.on(
    events.NewMessage(
        pattern=r"^/stop$"
    )
)
async def stop_command(event):

    user_id = event.sender_id

    client = user_clients.get(
        user_id
    )

    if not client:

        await event.reply(
            "🔴 Your userbot isn't running."
        )

        return

    try:

        await client.disconnect()

    except Exception:
        pass

    user_clients.pop(
        user_id,
        None
    )

    await event.reply(
        "⏹ <b>Your userbot has been stopped.</b>",
        parse_mode="html"
    )


@bot.on(
    events.NewMessage(
        pattern=r"^/logout$"
    )
)
async def logout_command(event):

    user_id = event.sender_id

    client = user_clients.get(
        user_id
    )

    if client:

        try:
            await client.log_out()

        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass

        user_clients.pop(
            user_id,
            None
        )

    delete_user(
        user_id
    )

    user_features.pop(
        user_id,
        None
    )

    await event.reply(
        """
🗑 <b>Account disconnected.</b>

Your stored session has been removed.

Use /login to connect again.
""",
        parse_mode="html"
    )


# ============================================================
# AUTO LOAD USERS
# ============================================================

async def load_existing_users():

    rows = db.execute(
        """
        SELECT telegram_id
        FROM users
        WHERE enabled = 1
        """
    ).fetchall()

    for (owner_id,) in rows:

        session = load_session(
            owner_id
        )

        if not session:
            continue

        client = await start_userbot(
            owner_id,
            session
        )

        if client:

            log.info(
                "Restored userbot %s",
                owner_id
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    log.info(
        "Starting bot..."
    )

    await bot.start(
        bot_token=BOT_TOKEN
    )

    log.info(
        "Bot started."
    )

    await load_existing_users()

    log.info(
        "All userbots loaded."
    )

    await bot.run_until_disconnected()


if __name__ == "__main__":

    asyncio.run(
        main()
    )