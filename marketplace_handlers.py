# marketplace_handlers.py

import logging
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes
from telegram import Update
from telegram.ext import ContextTypes
from kitchen_context import require


log = logging.getLogger("MARKETPLACE")


# ---------
# Keyboards
# ---------

def kb_kitchen_select():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍽 Заведение 1", callback_data="marketplace:kitchen:1")],
        [InlineKeyboardButton("🍽 Заведение 2", callback_data="marketplace:kitchen:2")],
    ])


# ---------
# Handlers
# ---------

async def marketplace_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # сбрасываем выбранную кухню
    context.user_data.pop("kitchen_id", None)
    context.user_data.pop("spreadsheet_id", None)

    await q.message.delete()

    await q.message.bot.send_message(
        chat_id=q.message.chat_id,
        text="Выберите заведение:",
        reply_markup=kb_kitchen_select(),
    )

async def marketplace_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /market — entry point for marketplace
    """
    # сбрасываем активную кухню
    context.user_data.pop("kitchen_id", None)

    # safety: команда может прийти без message (редко, но бывает)
    if not update.message:
        return

    await update.message.reply_text(
        "Выберите заведение:",
        reply_markup=kb_kitchen_select(),
    )


async def marketplace_select_kitchen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        _, _, kitchen_id_str = q.data.split(":", 2)
        kitchen_id = int(kitchen_id_str)
    except Exception:
        log.warning(f"Bad kitchen select callback: {q.data}")
        return

    # MVP SHORT-CIRCUIT
    # кухня 1 живет БЕЗ registry
    if kitchen_id == 1:
        context.user_data["kitchen_id"] = 1
        try:
            from main import render_home

            await q.message.delete()
            await render_home(context, q.message.chat_id)
            return
        except Exception:
            log.exception("Failed to render home for kitchen 1")
            await q.edit_message_text("Ошибка перехода на страницу заведения")
            return

    # дальше — ТОЛЬКО registry кухни
    try:
        kitchen = require(kitchen_id)
    except Exception as e:
        log.error(f"Kitchen select failed: {e}")
        await q.edit_message_text("Заведение недоступна")
        return

    context.user_data["kitchen_id"] = kitchen.kitchen_id
    context.user_data["spreadsheet_id"] = kitchen.spreadsheet_id

    await q.edit_message_text(
        text=(
            f"<b>{kitchen.name}</b>\n"
            f"Город: {kitchen.city}\n\n"
            "Страница заведения открыта. Можно оформлять заказ."
        ),
        parse_mode="HTML",
    )

async def marketplace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""

    # ожидаем формат: market:kitchen:<id>
    parts = data.split(":")
    if len(parts) != 3:
        return

    _, _, kitchen_id = parts

    # фиксируем выбранную кухню
    context.user_data["kitchen_id"] = kitchen_id

    # можно сохранить еще имя кухни, если хочешь
    # context.user_data["kitchen_name"] = ...

    # после выбора — обычный home
    await q.message.delete()
    await render_home(context, q.message.chat_id)