# broadcast.py
print("### BROADCAST FILE:", __file__)
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from kitchen_context import _REGISTRY
from sheets_repo import get_sheets_service

log = logging.getLogger("Broadcast")

# ===== helpers =====

def is_kitchen_admin(chat_id: int, kitchen) -> bool:
    return chat_id == kitchen.owner_chat_id or chat_id in kitchen.staff_chat_ids


def get_all_user_ids(sheet_service, spreadsheet_id: str) -> list[int]:
    sheet = sheet_service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range="users!A2:A",
    ).execute()

    rows = result.get("values", [])
    ids = []
    for r in rows:
        if r and r[0].isdigit():
            ids.append(int(r[0]))
    return ids


def get_service(context):
    if context.bot_data.get("SHEETS_SERVICE") is None:
        context.bot_data["SHEETS_SERVICE"] = get_sheets_service()
    return context.bot_data["SHEETS_SERVICE"]


# ===== handlers =====

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # kitchen_id должен быть уже определен (например, из текущего контекста стафа)
    kitchen_id = context.user_data.get("kitchen_id")
    if not kitchen_id:
        log.warning("start_broadcast called without kitchen_id in user_data")
        return

    kitchen = _REGISTRY.get(kitchen_id)
    if not kitchen:
        log.warning(f"start_broadcast: kitchen not found ({kitchen_id})")
        return

    if not is_kitchen_admin(chat_id, kitchen):
        log.warning(
            f"Unauthorized broadcast start | user={chat_id} kitchen={kitchen_id}"
        )
        return

    context.user_data["broadcast"] = {
        "kitchen_id": kitchen_id,
    }

    await update.message.reply_text(
        "📢 <b>Рассылка</b>\n\n"
        "Введите текст сообщения для рассылки ⬇️",
        parse_mode=ParseMode.HTML,
    )


async def on_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "broadcast" not in context.user_data:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    broadcast = context.user_data["broadcast"]
    broadcast["text"] = text

    kitchen_id = broadcast.get("kitchen_id")
    kitchen = _REGISTRY.get(kitchen_id)
    if not kitchen:
        log.warning(f"on_broadcast_text: kitchen not found ({kitchen_id})")
        return

    service = get_service(context)
    spreadsheet_id = kitchen.spreadsheet_id

    all_ids = get_all_user_ids(service, spreadsheet_id)

    owner = kitchen.owner_chat_id
    staff = kitchen.staff_chat_ids

    recipients = [uid for uid in all_ids if uid != owner and uid not in staff]
    broadcast["recipients"] = recipients

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Отправить", callback_data="broadcast:send"),
            InlineKeyboardButton("❌ Отмена", callback_data="broadcast:cancel"),
        ]
    ])

    await update.message.reply_text(
        "📝 <b>Превью рассылки</b>\n\n"
        f"{text}\n\n"
        f"👥 Получателей: <b>{len(recipients)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def on_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    chat_id = q.message.chat_id

    if data == "broadcast:cancel":
        context.user_data.pop("broadcast", None)
        await q.edit_message_text("❌ Рассылка отменена.")
        return

    if data != "broadcast:send":
        return

    broadcast = context.user_data.get("broadcast")
    if not broadcast:
        return

    kitchen_id = broadcast.get("kitchen_id")
    kitchen = _REGISTRY.get(kitchen_id)
    if not kitchen:
        log.warning(f"on_broadcast_confirm: kitchen not found ({kitchen_id})")
        return

    if not is_kitchen_admin(chat_id, kitchen):
        log.warning(
            f"Unauthorized broadcast send | user={chat_id} kitchen={kitchen_id}"
        )
        return

    text = broadcast["text"]
    recipients = broadcast["recipients"]

    await q.edit_message_text(
        f"🚀 Рассылка запущена\n\n"
        f"Сообщений: {len(recipients)}"
    )

    sent = 0
    failed = 0

    for uid in recipients:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            await asyncio.sleep(0.1)

    context.user_data.pop("broadcast", None)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "📊 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: <b>{sent}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>"
        ),
        parse_mode=ParseMode.HTML,
    )


# ===== register =====

def register_broadcast_handlers(app):
    app.add_handler(CommandHandler("broadcast", start_broadcast))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            on_broadcast_text
        ),
        group=0
    )
    app.add_handler(
        CallbackQueryHandler(on_broadcast_confirm, pattern=r"^broadcast:")
    )
    print("BROADCAST MODULE LOADED")
