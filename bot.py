"""Replit-ready Telegram post maker bot.

Set BOT_TOKEN and OWNER_IDS in environment variables, then run `python bot.py`.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

def load_dotenv_file(path: str = ".env") -> None:
    env = Path(path)
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\'\""))


load_dotenv_file()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
LOGGER = logging.getLogger("post-maker-bot")
DB_PATH = Path(os.getenv("DB_PATH", "postmaker.sqlite3"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
DEFAULT_FORCE_CHANNELS = [
    {"title": "AbtColombus", "chat_id": "@AbtColombus", "invite_link": "https://t.me/AbtColombus"},
]
BUTTON_EMOJI_IDS = [
    5219899949281453881, 5222472119295684375, 5222108309795908493, 5219672809936006424,
    5244820603663296299, 5219943216781995020, 5222400230133081714, 5222148368955877900,
    5246794802560774143, 5220053623211305785, 5220197908342648622, 5222241728659988366,
    5260424249914435335, 5219901967916084166, 5217890643321300022, 5246863809800318186,
    5247213725080890199, 5258023599419171861, 5220070652756635426, 5246942081284320100,
    5220046725493828505, 5303396278179210513, 5276489300207217985, 5294524383279198295,
    5294096239464295059, 5364174510708764528, 5294527084813626369, 5294017134756636818,
    5332423642850536254, 5264892613630111886, 5301096984617166561, 5301275719681190738,
    5310224206732996002, 5377377257356537351, 5314413943035278948, 5386521874089914548,
]
CAPTION_EMOJI_IDS = [
    6170163662544707658, 6294142703907116473, 6294106669131503002, 6176905893616031802,
    6091456153462512920, 6176742294016760397, 6293797538860373333, 5318938025361679130,
    5316657943188349246, 5316971840873177080, 6129812419028982717, 6129705083501293112,
    6129801569941592173, 6129650399977675538, 6129769198773083022, 6131886699254388574,
    6129572317472233948, 6129817830687775854, 6129653943325694007, 6129488844782836766,
    6129891098534877664, 6129625171339778354, 6129782839589214594, 6129771638314523716,
    6129444065453808638, 6129828611055689014,
]
VISIBLE_EMOJI = ["💎", "✨", "🚀", "🎨", "📝", "📌", "🌈", "⚡", "🔥", "🎉", "🔗", "⭐"]
ANIMATIONS = ["🎉", "🎊", "✨", "💫", "🚀", "🌈", "🥳", "⚡"]


def premium_emoji(emoji_id: int) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">✨</tg-emoji>'


def caption_icon(index: int = 0) -> str:
    return premium_emoji(CAPTION_EMOJI_IDS[index % len(CAPTION_EMOJI_IDS)])


def btn(text: str, tone: str = "primary", emoji_index: int = 0) -> str:
    # Bot API does not support custom emoji entities inside button text; semantic colors are represented by icons.
    color = {"success": "🟢", "danger": "🔴", "primary": "🔵", "neutral": "⚪"}.get(tone, "🔵")
    return f"{color} {VISIBLE_EMOJI[emoji_index % len(VISIBLE_EMOJI)]} {text}"


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS force_channels (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, chat_id TEXT NOT NULL, invite_link TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS more_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
            """
        )
        if not db.execute("SELECT 1 FROM force_channels LIMIT 1").fetchone():
            for pos, channel in enumerate(DEFAULT_FORCE_CHANNELS):
                db.execute("INSERT INTO force_channels(title, chat_id, invite_link, position) VALUES(?,?,?,?)", (channel["title"], channel["chat_id"], channel["invite_link"], pos))
        for owner_id in OWNER_IDS:
            db.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (owner_id,))
        db.commit()


def rows(sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, args).fetchall()


def execute(sql: str, args: tuple[Any, ...] = ()) -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(sql, args)
        db.commit()


def is_admin(user_id: int) -> bool:
    return bool(rows("SELECT 1 FROM admins WHERE user_id=?", (user_id,))) or user_id in OWNER_IDS


def save_user(update: Update) -> None:
    user = update.effective_user
    if user:
        execute("INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)", (user.id, user.username, user.first_name))


def force_channels() -> list[sqlite3.Row]:
    return rows("SELECT * FROM force_channels ORDER BY position, id")


async def has_joined_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    for channel in force_channels():
        try:
            member = await context.bot.get_chat_member(channel["chat_id"], user.id)
            if member.status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                return False
        except TelegramError as exc:
            LOGGER.warning("Force-join check failed for %s: %s", channel["chat_id"], exc)
            return False
    return True


def force_join_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(btn(channel["title"], "primary", i), url=channel["invite_link"])] for i, channel in enumerate(force_channels())]
    keyboard.append([InlineKeyboardButton(btn("I Joined", "success", 8), callback_data="check_join")])
    return InlineKeyboardMarkup(keyboard)


def main_keyboard() -> ReplyKeyboardMarkup:
    rows_ = [
        [KeyboardButton("📌 Create Post"), KeyboardButton("📝 Edit Draft")],
        [KeyboardButton("🎨 Font Changer"), KeyboardButton("🤖 More Bots")],
        [KeyboardButton("📣 Share Bot"), KeyboardButton("❓ How to Use")],
        [KeyboardButton("🛡 Admin Panel")],
    ]
    return ReplyKeyboardMarkup(rows_, resize_keyboard=True)


def main_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn("Create Post", "success", 0), callback_data="create_post"), InlineKeyboardButton(btn("Font Changer", "primary", 3), callback_data="font_menu")],
        [InlineKeyboardButton(btn("More Bots", "primary", 5), callback_data="more_bots"), InlineKeyboardButton(btn("Share Bot", "success", 6), callback_data="share_bot")],
        [InlineKeyboardButton(btn("How to Use", "neutral", 10), callback_data="help")],
    ])


def welcome_text(user_name: str) -> str:
    return (
        f"{caption_icon(0)} <b>Welcome, {html.escape(user_name)}!</b>\n\n"
        f"{caption_icon(1)} Create premium-looking Telegram posts with images, captions, colored-style buttons, links, fonts, and animations.\n"
        f"{caption_icon(2)} Developer: @AbtColombus\n\n"
        f"{caption_icon(3)} Choose an option below to start building your next post."
    )


@dataclass
class Draft:
    caption: str = ""
    photo_file_id: str | None = None
    buttons: list[dict[str, str]] = field(default_factory=list)


def draft(context: ContextTypes.DEFAULT_TYPE) -> Draft:
    data = context.user_data.setdefault("draft", Draft())
    if isinstance(data, dict):
        data = Draft(**data)
        context.user_data["draft"] = data
    return data


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_user(update)
    if not await has_joined_all(update, context):
        await update.effective_message.reply_html(
            f"{caption_icon(4)} <b>Join required channels to unlock the bot.</b>\n\n"
            "Tap every channel below, join or request access, then press <b>I Joined</b>.",
            reply_markup=force_join_keyboard(),
        )
        return
    await show_menu(update, context)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_html(welcome_text(user.first_name if user else "Creator"), reply_markup=main_keyboard())
    await update.effective_message.reply_html(f"{caption_icon(5)} <b>Main Menu</b>", reply_markup=main_inline())


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Checking your membership...")
    if await has_joined_all(update, context):
        await query.edit_message_text(f"{ANIMATIONS[0]} Access unlocked! Opening menu...", parse_mode=ParseMode.HTML)
        await show_menu(update, context)
    else:
        await query.edit_message_text(
            f"{caption_icon(6)} <b>Not joined yet.</b> Please join every required channel or wait for private-channel approval.",
            parse_mode=ParseMode.HTML,
            reply_markup=force_join_keyboard(),
        )


async def create_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["state"] = "await_caption"
    context.user_data["draft"] = Draft()
    await update.effective_message.reply_html(
        f"{caption_icon(7)} <b>Post Wizard</b>\nSend your caption text now. You can include Telegram HTML such as <b>bold</b>, <i>italic</i>, links, and premium emoji markup."
    )


FONT_MAPS = {
    "bold": str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭0123456789"),
    "mono": str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉0123456789"),
}


def font_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn("Bold Font", "primary", 1), callback_data="font:bold"), InlineKeyboardButton(btn("Mono Font", "primary", 2), callback_data="font:mono")],
        [InlineKeyboardButton(btn("Skip", "success", 3), callback_data="font:skip")],
    ])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_user(update)
    text = update.message.text or ""
    state = context.user_data.get("state")
    if text == "📌 Create Post":
        return await create_post(update, context)
    if text == "🎨 Font Changer":
        context.user_data["state"] = "font_tool"
        return await update.message.reply_text("Send text to convert into stylish fonts.")
    if text == "🤖 More Bots":
        return await more_bots(update, context)
    if text == "📣 Share Bot":
        return await share_bot(update, context)
    if text == "❓ How to Use":
        return await help_text(update, context)
    if text == "🛡 Admin Panel" or text == "/admin":
        return await admin_panel(update, context)
    if state == "await_caption":
        draft(context).caption = text
        context.user_data["state"] = "await_photo"
        return await update.message.reply_html(f"{caption_icon(8)} Caption saved. Send a photo now, or type /skip.", reply_markup=font_keyboard())
    if state == "await_button":
        parts = text.split("|", 2)
        if len(parts) < 2:
            return await update.message.reply_text("Use: Button Text | https://example.com | green/blue/red")
        tone = {"green": "success", "red": "danger", "blue": "primary"}.get(parts[2].strip().lower() if len(parts) > 2 else "blue", "primary")
        draft(context).buttons.append({"text": parts[0].strip(), "url": parts[1].strip(), "tone": tone})
        return await update.message.reply_text("Button added. Send another or /done.")
    if state == "font_tool":
        await update.message.reply_text(f"Bold:\n{text.translate(FONT_MAPS['bold'])}\n\nMono:\n{text.translate(FONT_MAPS['mono'])}")
        return
    await update.message.reply_text("Choose an option from the menu.", reply_markup=main_keyboard())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") == "await_photo":
        draft(context).photo_file_id = update.message.photo[-1].file_id
        context.user_data["state"] = "await_button"
        await update.message.reply_text("Photo saved. Add buttons as: Button Text | https://example.com | green/blue/red, or /done.")


async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") == "await_photo":
        context.user_data["state"] = "await_button"
        await update.message.reply_text("Photo skipped. Add buttons as: Button Text | https://example.com | green/blue/red, or /done.")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = draft(context)
    keyboard = [[InlineKeyboardButton(btn(b["text"], b["tone"], i), url=b["url"])] for i, b in enumerate(d.buttons)]
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(random.choice(ANIMATIONS) + " Your post is ready!")
    if d.photo_file_id:
        await update.message.reply_photo(d.photo_file_id, caption=d.caption, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.message.reply_html(d.caption or "Untitled post", reply_markup=markup)
    context.user_data["state"] = None


async def font_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    choice = query.data.split(":", 1)[1]
    d = draft(context)
    if choice in FONT_MAPS:
        d.caption = d.caption.translate(FONT_MAPS[choice])
    await query.answer("Font updated" if choice in FONT_MAPS else "Skipped")
    await query.edit_message_text("Now send a photo, or type /skip.")


async def help_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        f"{caption_icon(9)} <b>How to use</b>\n"
        "1. Tap Create Post.\n2. Send a caption.\n3. Optionally change font.\n4. Send a photo or /skip.\n5. Add URL buttons using: <code>Text | URL | green</code>.\n6. Type /done to generate and forward anywhere."
    )


async def share_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    me = await context.bot.get_me()
    url = f"https://t.me/{me.username}?start=share"
    await update.effective_message.reply_text("Share this bot with friends if you like it:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(btn("Share Bot", "success", 4), url=f"https://t.me/share/url?url={url}")]]))


async def more_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bots = rows("SELECT * FROM more_bots ORDER BY position, id")
    if not bots:
        return await update.effective_message.reply_html(f"{caption_icon(10)} <b>No more bots available now.</b>")
    keyboard = [[InlineKeyboardButton(btn(bot["title"], "primary", i), url=bot["url"])] for i, bot in enumerate(bots)]
    caption = "\n".join(f"• <b>{html.escape(bot['title'])}</b> — {html.escape(bot['description'])}" for bot in bots)
    await update.effective_message.reply_html(f"{caption_icon(11)} <b>More Bots</b>\n\n{caption}", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return await update.effective_message.reply_text("Admin only.")
    await update.effective_message.reply_html(
        "<b>Admin commands</b>\n"
        "/addchannel title | chat_id | invite_link\n/rmchannel id\n/movechannel id | position\n/addadmin user_id\n/rmadmin user_id\n/addbot title | description | url\n/rmbot id\n/broadcast text"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    cmd, _, payload = update.message.text.partition(" ")
    try:
        if cmd == "/addchannel":
            title, chat_id, invite = [p.strip() for p in payload.split("|", 2)]
            execute("INSERT INTO force_channels(title, chat_id, invite_link, position) VALUES(?,?,?,?)", (title, chat_id, invite, len(force_channels())))
            return await update.message.reply_text("Channel added.")
        if cmd == "/rmchannel":
            execute("DELETE FROM force_channels WHERE id=?", (int(payload),))
            return await update.message.reply_text("Channel removed.")
        if cmd == "/movechannel":
            cid, pos = [int(p.strip()) for p in payload.split("|", 1)]
            execute("UPDATE force_channels SET position=? WHERE id=?", (pos, cid))
            return await update.message.reply_text("Channel reordered.")
        if cmd == "/addadmin":
            execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (int(payload),))
            return await update.message.reply_text("Admin added.")
        if cmd == "/rmadmin":
            execute("DELETE FROM admins WHERE user_id=?", (int(payload),))
            return await update.message.reply_text("Admin removed.")
        if cmd == "/addbot":
            title, desc, url = [p.strip() for p in payload.split("|", 2)]
            execute("INSERT INTO more_bots(title, description, url, position) VALUES(?,?,?,?)", (title, desc, url, 0))
            return await update.message.reply_text("Bot added.")
        if cmd == "/rmbot":
            execute("DELETE FROM more_bots WHERE id=?", (int(payload),))
            return await update.message.reply_text("Bot removed.")
        if cmd == "/broadcast":
            users = rows("SELECT user_id FROM users")
            ok = fail = 0
            for user in users:
                try:
                    await context.bot.send_message(user["user_id"], payload)
                    ok += 1
                    await asyncio.sleep(0.04)
                except (Forbidden, BadRequest):
                    fail += 1
            return await update.message.reply_text(f"Broadcast done. Sent: {ok}, failed: {fail}")
    except Exception as exc:  # noqa: BLE001 - admin gets actionable syntax feedback.
        return await update.message.reply_text(f"Command failed: {exc}")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "check_join":
        return await check_join(update, context)
    if data == "create_post":
        await update.callback_query.answer()
        return await create_post(update, context)
    if data == "font_menu":
        await update.callback_query.answer()
        context.user_data["state"] = "font_tool"
        return await update.callback_query.message.reply_text("Send text to convert into stylish fonts.")
    if data == "more_bots":
        await update.callback_query.answer()
        return await more_bots(update, context)
    if data == "share_bot":
        await update.callback_query.answer()
        return await share_bot(update, context)
    if data == "help":
        await update.callback_query.answer()
        return await help_text(update, context)
    if data.startswith("font:"):
        return await font_action(update, context)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(MessageHandler(filters.Regex(r"^/(addchannel|rmchannel|movechannel|addadmin|rmadmin|addbot|rmbot|broadcast)"), admin_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
