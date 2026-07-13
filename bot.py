from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import sqlite3
import urllib.parse
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
    ChatJoinRequestHandler,
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

# Predefined Telegram Message Effects (confetti, fireworks, stars)
MESSAGE_EFFECTS = ["5104841245755180586", "5107584321108051014", "5044134455711629726", "5123236135417415011"]


def premium_emoji(emoji_id: int) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">✨</tg-emoji>'


def caption_icon(index: int = 0) -> str:
    return premium_emoji(CAPTION_EMOJI_IDS[index % len(CAPTION_EMOJI_IDS)])


def bot_text(message: str, icon_index: int = 0) -> str:
    return f"{caption_icon(icon_index)} {message}"


def btn(text: str, tone: str = "primary", emoji_index: int = 0) -> str:
    fallback = {"success": "🟢", "danger": "🔴", "primary": "🔵", "neutral": "⚪"}.get(tone, "🔵")
    return f"{fallback} {VISIBLE_EMOJI[emoji_index % len(VISIBLE_EMOJI)]} {text}"


def styled_button(text: str, tone: str = "primary", emoji_index: int = 0, **kwargs: Any) -> InlineKeyboardButton:
    api_kwargs = kwargs.pop("api_kwargs", {}) or {}
    api_kwargs.setdefault("style", tone if tone in {"success", "danger", "primary"} else "primary")
    api_kwargs.setdefault("icon_custom_emoji_id", str(BUTTON_EMOJI_IDS[emoji_index % len(BUTTON_EMOJI_IDS)]))
    return InlineKeyboardButton(text=text, api_kwargs=api_kwargs, **kwargs)


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS force_channels (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, chat_id TEXT NOT NULL, invite_link TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS more_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS join_requests (user_id INTEGER, chat_id TEXT, PRIMARY KEY(user_id, chat_id));
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


async def check_and_register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    exists = bool(rows("SELECT 1 FROM users WHERE user_id=?", (user.id,)))
    if not exists:
        execute("INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)", (user.id, user.username, user.first_name))
        
        # User count
        total_users = rows("SELECT COUNT(*) as count FROM users")[0]["count"]
        
        # Notify admins
        admin_rows = rows("SELECT user_id FROM admins")
        admin_ids = {r["user_id"] for r in admin_rows} | OWNER_IDS
        
        username_str = f"@{user.username}" if user.username else "No username"
        notification_text = (
            f"👤 <b>New User Registered!</b>\n\n"
            f"• <b>Name:</b> {html.escape(user.first_name)}\n"
            f"• <b>ID:</b> <code>{user.id}</code>\n"
            f"• <b>Username:</b> {username_str}\n\n"
            f"📊 <b>Total Users:</b> <code>{total_users}</code>"
        )
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                LOGGER.warning("Could not notify admin %s of new user: %s", admin_id, e)


def force_channels() -> list[sqlite3.Row]:
    return rows("SELECT * FROM force_channels ORDER BY position, id")


async def has_joined_channel(bot, user_id: int, chat_id: str) -> bool:
    # 1. Check if we have recorded a pending request for this chat ID or username
    chat_id_str = str(chat_id).strip()
    is_requested = bool(rows("SELECT 1 FROM join_requests WHERE user_id=? AND chat_id=?", (user_id, chat_id_str)))
    if is_requested:
        return True

    # 2. Query Telegram API for direct status
    try:
        try:
            target_id = int(chat_id_str)
        except ValueError:
            target_id = chat_id_str

        member = await bot.get_chat_member(target_id, user_id)
        if member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
            return True
    except TelegramError as exc:
        LOGGER.warning("Force-join check failed for %s: %s", chat_id, exc)
    return False


async def unjoined_force_channels(bot, user_id: int) -> list[sqlite3.Row]:
    unjoined = []
    for channel in force_channels():
        joined = await has_joined_channel(bot, user_id, channel["chat_id"])
        if not joined:
            unjoined.append(channel)
    return unjoined


async def has_joined_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    unjoined = await unjoined_force_channels(context.bot, user.id)
    return len(unjoined) == 0


async def force_join_keyboard_for_user(bot, user_id: int) -> InlineKeyboardMarkup:
    unjoined = await unjoined_force_channels(bot, user_id)
    keyboard = []
    for i, channel in enumerate(unjoined):
        keyboard.append([styled_button(channel["title"], "primary", i, url=channel["invite_link"])])
    keyboard.append([styled_button("I Joined", "success", 8, callback_data="check_join")])
    return InlineKeyboardMarkup(keyboard)


def main_keyboard() -> ReplyKeyboardMarkup:
    # Using real Bot API 9.4 styles and custom premium emojis for the main menu layout
    rows_ = [
        [
            KeyboardButton("🟢 Create Post", style="success", icon_custom_emoji_id="5219899949281453881"),
            KeyboardButton("🔵 Font Changer", style="primary", icon_custom_emoji_id="5222472119295684375")
        ],
        [
            KeyboardButton("🔵 More Bots", style="primary", icon_custom_emoji_id="5244820603663296299"),
            KeyboardButton("🟢 Share Bot", style="success", icon_custom_emoji_id="5222400230133081714")
        ],
        [
            KeyboardButton("⚪ How to Use", style="primary", icon_custom_emoji_id="5219901967916084166")
        ]
    ]
    return ReplyKeyboardMarkup(rows_, resize_keyboard=True, persistent=True, input_field_placeholder="✨ Choose a premium tool")


def welcome_text(user_name: str) -> str:
    return (
        f"{caption_icon(0)} <b>Welcome, {html.escape(user_name)}!</b>\n\n"
        f"{caption_icon(1)} Create premium-looking Telegram posts with images, captions, colored-style buttons, links, fonts, and animations.\n"
        f"{caption_icon(2)} Developer: @AbtColombus\n\n"
        f"{caption_icon(3)} Choose an option below to start building your next post."
    )


@dataclass
class Draft:
    text: str = ""
    caption: str = ""
    media_type: str = "text"
    file_id: str | None = None
    buttons: list[dict[str, str]] = field(default_factory=list)
    pending_button_text: str = ""
    pending_button_tone: str = "primary"


def draft(context: ContextTypes.DEFAULT_TYPE) -> Draft:
    data = context.user_data.setdefault("draft", Draft())
    if isinstance(data, dict):
        data = Draft(**data)
        context.user_data["draft"] = data
    return data


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await check_and_register_user(update, context)
    user = update.effective_user
    unjoined = await unjoined_force_channels(context.bot, user.id)
    if len(unjoined) > 0:
        kb = await force_join_keyboard_for_user(context.bot, user.id)
        await update.effective_message.reply_html(
            f"{caption_icon(4)} <b>Join required channels to unlock the bot.</b>\n\n"
            "Tap every channel below, join or request access, then press <b>I Joined</b>.",
            reply_markup=kb,
        )
        return
    await show_menu(update, context)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    effect_id = random.choice(MESSAGE_EFFECTS)
    await update.effective_message.reply_html(
        welcome_text(user.first_name if user else "Creator"), 
        reply_markup=main_keyboard(),
        message_effect_id=effect_id
    )
    await update.effective_message.reply_html(
        bot_text("<b>Main Menu</b> — use the large keyboard buttons below.", 5), 
        reply_markup=main_keyboard(),
        message_effect_id=effect_id
    )


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Checking your membership...")
    unjoined = await unjoined_force_channels(context.bot, update.effective_user.id)
    if len(unjoined) == 0:
        await query.edit_message_text(f"{ANIMATIONS[0]} Access unlocked! Opening menu...", parse_mode=ParseMode.HTML)
        await show_menu(update, context)
    else:
        kb = await force_join_keyboard_for_user(context.bot, update.effective_user.id)
        await query.edit_message_text(
            f"{caption_icon(6)} <b>Not joined yet.</b> Please join every required channel or wait for private-channel approval.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )


async def create_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["state"] = "await_post"
    context.user_data["draft"] = Draft()
    await update.effective_message.reply_html(
        bot_text(
            "<b>Send or forward your complete post here.</b>\n\n"
            "Forward an existing post or send a fresh message with a photo, video, GIF/animation, document, or text. "
            "If you send media, include the caption in the same message so the bot can preserve it beautifully.\n\n"
            "After I save it, you can add unlimited premium buttons: first send the button text, then choose Success/Danger/Primary, then send the URL.",
            7,
        ),
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Cancel", style="danger", icon_custom_emoji_id="5246863809800318186")]],
            resize_keyboard=True,
            persistent=True
        )
    )


def map_font(src: str, lower: str, upper: str | None = None, digits: str = "0123456789") -> dict[int, str]:
    upper = upper or lower.upper()
    return make_font(lower + upper + digits)


ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def make_font(target: str) -> dict[int, str]:
    return {ord(src): dst for src, dst in zip(ALPHABET, target)}


FONT_MAPS = {
    "bold": make_font("𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭0123456789"),
    "italic": make_font("𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝗸𝘭𝘮𝘯𝘰𝘱𝗲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡0123456789"),
    "bold_italic": make_font("𝙖𝙗𝙘𝙙𝙚𝙛𝙜𝙝𝙞𝙟𝙠𝙡𝙢𝙣𝙤𝙥𝙦𝙧🇸𝙩𝙪𝙫𝙬𝙭𝙮𝙯𝘼𝘽𝘾𝘿🇪𝙁𝙂𝙃𝙄𝙅𝙆𝙇🇲🇳𝙊𝙋𝙌𝙍𝙎𝙏𝙐𝙑𝙒𝙓𝙔𝙕0123456789"),
    "script": make_font("𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵0123456789"),
    "bold_script": make_font("𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩0123456789"),
    "fraktur": make_font("𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ0123456789"),
    "double": make_font("𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ0123456789"),
    "mono": make_font("𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛🇸𝚝𝚞𝚟𝚠𝚡𝚢𝚣𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁🇸𝚃𝚄𝚅𝚆𝚇𝚈𝚉0123456789"),
    "sans": make_font("𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹0123456789"),
    "sans_bold": make_font("𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸 codebase𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝗔𝗕🇨🇩🇪🇫🇬𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭0123456789"),
    "wide": make_font("ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９"),
    "circled": make_font("ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ⓪①②③④⑤⑥⑦⑧⑨"),
    "small_caps": make_font("ᴀʙ🇨🇩ᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "upside": make_font("ɐqɔpǝɟƃɥᴉɾʞʃɯuodbɹsʇnʌʍxʎz∀𐐒ƆᗡƎℲ⅁HIſꓘ˥WNOԀΌᴚS⊥∩ΛMX⅄Z0ƖᄅƐㄣϛ9ㄥ86"),
    "mirror": make_font("ɒdɔbɘʇǫʜiႱʞlmᴎoqɿꙅƚυvwxʏzAꓭƆᗡƎꟻӘHIႱꓘ⅃MᴎOꟼỌЯꙄTUVWXYZ0123456789"),
    "squares": make_font("🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉0123456789"),
    "bubbles": make_font("🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩0123456789"),
    "slashes": None,
    "sparkles": None,
    "hearts": None,
    "arrows": None,
}


def apply_font(text: str, name: str) -> str:
    if name == "slashes":
        return " ".join(f"/{c}/" if c != " " else "   " for c in text)
    if name == "sparkles":
        return "✨" + "✨".join(text) + "✨"
    if name == "hearts":
        return "♡ " + " ♡ ".join(text.split()) + " ♡"
    if name == "arrows":
        return "➜ " + text.replace(" ", " ᐅ ")
    return text.translate(FONT_MAPS[name])


def font_keyboard(prefix: str = "font") -> InlineKeyboardMarkup:
    keys = list(FONT_MAPS.keys())
    keyboard = []
    for i in range(0, len(keys), 2):
        row = []
        for name in keys[i:i + 2]:
            sample = apply_font(name.replace("_", " "), name)[:18]
            row.append(styled_button(sample, "primary", i, callback_data=f"{prefix}:{name}"))
        keyboard.append(row)
    keyboard.append([styled_button("Skip Font", "success", 3, callback_data=f"{prefix}:skip")])
    return InlineKeyboardMarkup(keyboard)


def store_message_as_draft(message: Any, context: ContextTypes.DEFAULT_TYPE) -> Draft:
    d = draft(context)
    if message.photo:
        d.media_type, d.file_id = "photo", message.photo[-1].file_id
        d.caption = message.caption_html or message.caption or ""
    elif message.video:
        d.media_type, d.file_id = "video", message.video.file_id
        d.caption = message.caption_html or message.caption or ""
    elif message.animation:
        d.media_type, d.file_id = "animation", message.animation.file_id
        d.caption = message.caption_html or message.caption or ""
    elif message.document:
        d.media_type, d.file_id = "document", message.document.file_id
        d.caption = message.caption_html or message.caption or ""
    else:
        d.media_type, d.file_id = "text", None
        d.text = message.text_html or message.text or ""
    return d


def post_keyboard(d: Draft) -> InlineKeyboardMarkup | None:
    rows_ = []
    for i in range(0, len(d.buttons), 2):
        rows_.append([styled_button(b["text"], b["tone"], i + j, url=b["url"]) for j, b in enumerate(d.buttons[i:i + 2])])
    return InlineKeyboardMarkup(rows_) if rows_ else None


def builder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [styled_button("Add Button", "success", 0, callback_data="builder:add"), styled_button("Preview Post", "primary", 1, callback_data="builder:preview")],
        [styled_button("Finish", "success", 2, callback_data="builder:finish"), styled_button("Cancel", "danger", 3, callback_data="builder:cancel")],
    ])


def color_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [styled_button("Success", "success", 0, callback_data="color:success"), styled_button("Primary", "primary", 1, callback_data="color:primary"), styled_button("Danger", "danger", 2, callback_data="color:danger")]
    ])


async def send_draft(message: Any, d: Draft, preview: bool = False) -> None:
    markup = post_keyboard(d)
    intro = random.choice(ANIMATIONS) + (" Preview:" if preview else " Your premium post is ready!")
    effect_id = random.choice(MESSAGE_EFFECTS) if not preview else None
    
    await message.reply_html(bot_text(intro, 12), message_effect_id=effect_id)
    if d.media_type == "photo" and d.file_id:
        await message.reply_photo(d.file_id, caption=d.caption, parse_mode=ParseMode.HTML, reply_markup=markup, message_effect_id=effect_id)
    elif d.media_type == "video" and d.file_id:
        await message.reply_video(d.file_id, caption=d.caption, parse_mode=ParseMode.HTML, reply_markup=markup, message_effect_id=effect_id)
    elif d.media_type == "animation" and d.file_id:
        await message.reply_animation(d.file_id, caption=d.caption, parse_mode=ParseMode.HTML, reply_markup=markup, message_effect_id=effect_id)
    elif d.media_type == "document" and d.file_id:
        await message.reply_document(d.file_id, caption=d.caption, parse_mode=ParseMode.HTML, reply_markup=markup, message_effect_id=effect_id)
    else:
        await message.reply_html(d.text or d.caption or "Untitled post", reply_markup=markup, message_effect_id=effect_id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await check_and_register_user(update, context)
    text = update.message.text or ""
    state = context.user_data.get("state")
    
    # Strip emojis and helper characters for unified text comparison
    normalized = text.replace("🟢", "").replace("🔵", "").replace("🔴", "").replace("⚪", "").replace("🔙", "").replace("❌", "").strip()
    
    # Global back / cancel handler
    if "Back to Main Menu" in normalized or "Cancel" in normalized:
        context.user_data["state"] = None
        context.user_data["draft"] = Draft()
        await show_menu(update, context)
        return

    if "Create Post" in normalized:
        return await create_post(update, context)
        
    if "Font Changer" in normalized:
        context.user_data["state"] = "font_tool"
        await update.message.reply_html(
            bot_text("<b>Send any text.</b> I will show 20+ viral font styles with examples.", 13),
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🔙 Back to Main Menu", style="danger", icon_custom_emoji_id="5246863809800318186")]],
                resize_keyboard=True,
                persistent=True
            )
        )
        return
        
    if "More Bots" in normalized:
        return await more_bots(update, context)
    if "Share Bot" in normalized:
        return await share_bot(update, context)
    if "How to Use" in normalized:
        return await help_text(update, context)
    if text.strip() == "/admin":
        return await admin_panel(update, context)
        
    if state == "await_post":
        d = store_message_as_draft(update.message, context)
        context.user_data["state"] = None
        await update.message.reply_html(
            bot_text("<b>Post saved perfectly.</b> Now add premium buttons, preview it, or finish.", 8), 
            reply_markup=builder_keyboard(),
            message_effect_id=random.choice(MESSAGE_EFFECTS)
        )
        return
        
    if state == "await_button_text":
        draft(context).pending_button_text = text
        context.user_data["state"] = "await_button_color"
        return await update.message.reply_html(bot_text("<b>Choose button colour style:</b>", 14), reply_markup=color_keyboard())
        
    if state == "await_button_url":
        d = draft(context)
        d.buttons.append({"text": d.pending_button_text, "url": text.strip(), "tone": d.pending_button_tone})
        d.pending_button_text = ""
        context.user_data["state"] = None
        return await update.message.reply_html(bot_text(f"Button added. Total buttons: <b>{len(d.buttons)}</b>. Rows are arranged as 2 buttons per row.", 15), reply_markup=builder_keyboard())
        
    if state == "font_tool":
        context.user_data["font_source"] = text
        return await update.message.reply_html(bot_text("Pick a viral font style. Each button shows its own example.", 16), reply_markup=font_keyboard("toolfont"))
        
    await update.message.reply_html(bot_text("Choose an option from the main menu keyboard.", 17), reply_markup=main_keyboard())


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await check_and_register_user(update, context)
    if context.user_data.get("state") == "await_post":
        store_message_as_draft(update.message, context)
        context.user_data["state"] = None
        await update.message.reply_html(
            bot_text("<b>Post media and caption saved.</b> Add unlimited premium buttons or preview now.", 18), 
            reply_markup=builder_keyboard(),
            message_effect_id=random.choice(MESSAGE_EFFECTS)
        )


async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(bot_text("No skip needed now — just send/forward your post, or use the builder buttons.", 19))


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_draft(update.message, draft(context), preview=False)
    context.user_data["state"] = None


async def font_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    prefix, choice = query.data.split(":", 1)
    await query.answer("Font updated" if choice != "skip" else "Skipped")
    if choice != "skip" and prefix == "toolfont":
        source = context.user_data.get("font_source", "Premium Post Maker")
        return await query.edit_message_text(bot_text(f"<b>Font result:</b>\n<code>{html.escape(apply_font(source, choice))}</code>", 20), parse_mode=ParseMode.HTML)
    if choice != "skip":
        d = draft(context)
        d.caption = apply_font(d.caption, choice)
        d.text = apply_font(d.text, choice)
    await query.edit_message_text(bot_text("Font applied. Continue building your post.", 21), parse_mode=ParseMode.HTML, reply_markup=builder_keyboard())


async def builder_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    await query.answer()
    if action == "add":
        context.user_data["state"] = "await_button_text"
        return await query.message.reply_html(bot_text("Send the button text now. Premium emoji markup is allowed in the text you send.", 22))
    if action == "preview":
        return await send_draft(query.message, draft(context), preview=True)
    if action == "finish":
        await send_draft(query.message, draft(context), preview=False)
        context.user_data["state"] = None
        return
    if action == "cancel":
        context.user_data["state"] = None
        context.user_data["draft"] = Draft()
        return await query.message.reply_html(bot_text("Builder cancelled.", 23), reply_markup=main_keyboard())


async def color_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tone = query.data.split(":", 1)[1]
    d = draft(context)
    d.pending_button_tone = tone
    context.user_data["state"] = "await_button_url"
    await query.answer("Colour selected")
    await query.edit_message_text(bot_text("Now send the button URL, for example https://t.me/AbtColombus", 24), parse_mode=ParseMode.HTML)


async def help_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        bot_text(
            "<b>How to use the advanced post builder</b>\n\n"
            "1. Tap <b>Create Post</b>.\n"
            "2. Send or forward your complete post with image/video/GIF/document/text and caption.\n"
            "3. Tap <b>Add Button</b>, send button text, choose Success/Danger/Primary, then send URL.\n"
            "4. Add unlimited buttons. The bot arranges them as 2 buttons per row.\n"
            "5. Preview or Finish and forward the final post anywhere.",
            9,
        )
    )


async def share_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    me = await context.bot.get_me()
    bot_url = f"https://t.me/{me.username}?start=share"
    
    # Premium text content describing the bot for sharing
    share_text = (
        "🤖 <b>Premium Telegram Post Maker Bot!</b>\n\n"
        "Create premium-looking Telegram posts with beautifully styled color-highlighted buttons, custom fonts, images, and premium full-screen animations!\n\n"
        "Start crafting viral posts now: 👇"
    )
    encoded_text = urllib.parse.quote(share_text)
    share_url = f"https://t.me/share/url?url={bot_url}&text={encoded_text}"
    
    await update.effective_message.reply_html(
        bot_text(
            "<b>Share this bot with your friends!</b>\n\n"
            "Click the button below to share a beautiful referral post containing details and the bot's link with your friends or channels.",
            10
        ),
        reply_markup=InlineKeyboardMarkup([[styled_button("🚀 Share Bot Now", "success", 4, url=share_url)]]),
    )


async def more_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bots = rows("SELECT * FROM more_bots ORDER BY position, id")
    if not bots:
        return await update.effective_message.reply_html(bot_text("<b>No more bots available now.</b>", 11))
    keyboard = [[styled_button(bot["title"], "primary", i, url=bot["url"])] for i, bot in enumerate(bots)]
    caption = "\n".join(f"• <b>{html.escape(bot['title'])}</b> — {html.escape(bot['description'])}" for bot in bots)
    await update.effective_message.reply_html(bot_text(f"<b>More Bots</b>\n\n{caption}", 12), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return await update.effective_message.reply_html(bot_text("Admin only.", 13))
    await update.effective_message.reply_html(
        bot_text(
            "<b>Admin commands</b>\n"
            "/addchannel title | chat_id | invite_link\n/rmchannel id\n/movechannel id | position\n"
            "/addadmin user_id\n/rmadmin user_id\n/addbot title | description | url\n/rmbot id\n/broadcast text",
            14,
        )
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    cmd, _, payload = update.message.text.partition(" ")
    try:
        if cmd == "/addchannel":
            title, chat_id, invite = [p.strip() for p in payload.split("|", 2)]
            execute("INSERT INTO force_channels(title, chat_id, invite_link, position) VALUES(?,?,?,?)", (title, chat_id, invite, len(force_channels())))
            return await update.message.reply_html(bot_text("Channel added.", 15))
        if cmd == "/rmchannel":
            execute("DELETE FROM force_channels WHERE id=?", (int(payload),))
            return await update.message.reply_html(bot_text("Channel removed.", 16))
        if cmd == "/movechannel":
            cid, pos = [int(p.strip()) for p in payload.split("|", 1)]
            execute("UPDATE force_channels SET position=? WHERE id=?", (pos, cid))
            return await update.message.reply_html(bot_text("Channel reordered.", 17))
        if cmd == "/addadmin":
            execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (int(payload),))
            return await update.message.reply_html(bot_text("Admin added.", 18))
        if cmd == "/rmadmin":
            execute("DELETE FROM admins WHERE user_id=?", (int(payload),))
            return await update.message.reply_html(bot_text("Admin removed.", 19))
        if cmd == "/addbot":
            title, desc, url = [p.strip() for p in payload.split("|", 2)]
            execute("INSERT INTO more_bots(title, description, url, position) VALUES(?,?,?,?)", (title, desc, url, 0))
            return await update.message.reply_html(bot_text("Bot added.", 20))
        if cmd == "/rmbot":
            execute("DELETE FROM more_bots WHERE id=?", (int(payload),))
            return await update.message.reply_html(bot_text("Bot removed.", 21))
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
            return await update.message.reply_html(bot_text(f"Broadcast done. Sent: <b>{ok}</b>, failed: <b>{fail}</b>", 22))
    except Exception as exc:  # noqa: BLE001 - admin gets actionable syntax feedback.
        return await update.message.reply_html(bot_text(f"Command failed: <code>{html.escape(str(exc))}</code>", 23))


async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    if not request:
        return
    user = request.from_user
    chat = request.chat
    
    # Save the request status immediately
    chat_id_str = str(chat.id)
    execute("INSERT OR IGNORE INTO join_requests(user_id, chat_id) VALUES(?,?)", (user.id, chat_id_str))
    if chat.username:
        execute("INSERT OR IGNORE INTO join_requests(user_id, chat_id) VALUES(?,?)", (user.id, f"@{chat.username}"))
        
    # Send personal DM request to the user to start the bot
    bot_username = context.bot.username
    start_link = f"https://t.me/{bot_username}?start=join_request"
    
    dm_text = (
        f"✨ <b>Hello, {html.escape(user.first_name)}!</b>\n\n"
        f"Thank you for requesting to join <b>{html.escape(chat.title)}</b>.\n\n"
        f"To complete your request and approve your access, please start our official bot by tapping the button below! 👇"
    )
    keyboard = InlineKeyboardMarkup([[styled_button("🤖 Start Bot Now", "success", 0, url=start_link)]])
    
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=dm_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            message_effect_id=random.choice(MESSAGE_EFFECTS)
        )
    except Exception as exc:
        LOGGER.warning("Failed to DM join requester %s: %s", user.id, exc)

    # Automatically check if this was the last pending requirement
    unjoined = await unjoined_force_channels(context.bot, user.id)
    if len(unjoined) == 0:
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=welcome_text(user.first_name),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard(),
                message_effect_id=random.choice(MESSAGE_EFFECTS)
            )
        except Exception:
            pass


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "check_join":
        return await check_join(update, context)
    if data.startswith("font:") or data.startswith("toolfont:"):
        return await font_action(update, context)
    if data.startswith("builder:"):
        return await builder_action(update, context)
    if data.startswith("color:"):
        return await color_action(update, context)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(ChatJoinRequestHandler(handle_chat_join_request))
    app.add_handler(MessageHandler(filters.Regex(r"^/(addchannel|rmchannel|movechannel|addadmin|rmadmin|addbot|rmbot|broadcast)"), admin_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()