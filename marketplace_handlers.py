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
from kitchen_context import load_registry, require


log = logging.getLogger("MARKETPLACE")


# ---------
# Keyboards
# ---------

def kb_kitchen_select():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🥟 Восток & Азия", callback_data="marketplace:kitchen:kitchen_1")],
        [InlineKeyboardButton("🍣 Tokyo Roll", callback_data="marketplace:kitchen:kitchen_2")],
        [InlineKeyboardButton("🥘 Русский Дом", callback_data="marketplace:kitchen:kitchen_3")],
        [InlineKeyboardButton("🍔 Urban Grill", callback_data="marketplace:kitchen:kitchen_4")],
        [InlineKeyboardButton("🌯 Street Food Hub", callback_data="marketplace:kitchen:kitchen_5")],
    ])

# ---------
# helpers
# ---------

def get_active_kitchen(context):
    from kitchen_context import require, load_registry, RegistryNotLoaded, _REGISTRY

    kitchen_id = context.user_data.get("kitchen_id")

    try:
        # 1️⃣ если kitchen_id есть — пробуем его
        if kitchen_id:
            return require(kitchen_id)

    except RegistryNotLoaded:
        load_registry()
        if kitchen_id:
            try:
                return require(kitchen_id)
            except Exception:
                pass

    except Exception:
        pass

    # 2️⃣ fallback: берем первую активную кухню из реестра
    try:
        if not _REGISTRY:
            load_registry()

        for k in _REGISTRY.values():
            if getattr(k, "status", "active") == "active":
                context.user_data["kitchen_id"] = k.kitchen_id
                return k
    except Exception:
        pass

    # 3️⃣ если вообще ничего нет — это уже критика
    return None
# ---------
# Handlers
# ---------

async def marketplace_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    context.user_data.pop("kitchen_id", None)

    try:
        await q.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text="Выберите кухню:",
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

    data = q.data or ""

    # ожидаем: marketplace:kitchen:DASTARKHAN
    parts = data.split(":", 2)
    if len(parts) != 3:
        log.warning(f"Bad kitchen select callback: {data}")
        return

    _, _, kitchen_id = parts  # "DASTARKHAN" / "kitchen_2"

    # на всякий, чтобы registry был загружен
    try:
        from kitchen_context import load_registry
        load_registry()
    except Exception as e:
        log.error(f"Registry load failed: {e}")

    try:
        kitchen = require(kitchen_id)
    except Exception as e:
        log.error(f"Kitchen select failed: {e}")
        await q.edit_message_text("Кухня недоступна")
        return

    context.user_data["kitchen_id"] = kitchen.kitchen_id

    # если нам нужен сразу переход на обычный home, то лучше не edit, а удалить и рендерить home
    try:
        await q.message.delete()
    except Exception:
        pass
    from main import render_home
    await render_home(context, q.message.chat_id)

async def marketplace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data or ""

    # 🔁 возврат к выбору кухни
    if data == "market:back":
        context.user_data.pop("kitchen_id", None)
        await q.message.delete()
        await marketplace_start(update, context)
        return

    # ожидаем формат: market:kitchen:<id>
    parts = data.split(":")
    if len(parts) != 3:
        return

    _, _, kitchen_id = parts

    # фиксируем выбранную кухню
    context.user_data["kitchen_id"] = kitchen_id

    # после выбора — обычный home
    await q.message.delete()
    from main import render_home
    await render_home(context, q.message.chat_id)