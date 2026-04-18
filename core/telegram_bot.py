import os
import asyncio
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=BOT_TOKEN)
    return _bot


async def send_alert(message: str, buttons: list[dict] | None = None) -> int | None:
    """Send a Telegram message. Returns message_id or None on failure."""
    try:
        bot = get_bot()
        reply_markup = None
        if buttons:
            keyboard = [[InlineKeyboardButton(b["text"], callback_data=b["data"]) for b in row]
                        for row in buttons]
            reply_markup = InlineKeyboardMarkup(keyboard)

        msg = await bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return msg.message_id
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return None


async def send_confirmation(confirmation: dict) -> None:
    """Send a human-in-the-loop confirmation request."""
    ctype = confirmation.get("type", "unknown")
    msg = confirmation.get("message", "")
    conf_id = confirmation.get("id", "")

    buttons = [[
        {"text": "✅ Aprobar", "data": f"approve:{conf_id}"},
        {"text": "❌ Rechazar", "data": f"reject:{conf_id}"},
    ]]
    await send_alert(f"🔔 <b>Confirmación requerida</b> [{ctype}]\n\n{msg}", buttons)
