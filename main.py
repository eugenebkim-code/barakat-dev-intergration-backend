# main.py — FlowerShopKR DEMO "электронный прилавок"
# Требования:
# - Python + python-telegram-bot v20+
# - без AI/оплаты/админки
# - один ADMIN_CHAT_ID
# - "одно окно": при любом действии удаляем предыдущие сообщения бота и рисуем заново
#
# ENV:
#   BOT_TOKEN=...
#   ADMIN_CHAT_ID=123456789
#
# Файлы рядом:
#   main.py
#   catalog.py
#   flowers/... (пути в catalog.py -> Product.photo)
# IMPORTANT:
# ForceReply messages must be handled via filters.REPLY
# filters.TEXT is unreliable after callbacks + deleteMessage


import os
import logging
from typing import Dict, List, Optional
from contextlib import ExitStack
import json
from datetime import datetime, timedelta

from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

from telegram import ForceReply

from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from google.oauth2 import service_account
from googleapiclient.discovery import build

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

if not GOOGLE_CREDENTIALS_JSON or not SPREADSHEET_ID:
    raise RuntimeError("Google Sheets ENV vars missing")

GOOGLE_CREDS_INFO = json.loads(GOOGLE_CREDENTIALS_JSON)

# -------------------------
# logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("FlowerShopKR")


# -------------------------
# config
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")
if not OWNER_CHAT_ID:
    raise RuntimeError("OWNER_CHAT_ID is not set")

OWNER_CHAT_ID_INT = int(OWNER_CHAT_ID)


STAFF_CHAT_IDS = {
    int(x) for x in os.getenv("STAFF_CHAT_IDS", "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not ADMIN_CHAT_ID:
    raise RuntimeError("ADMIN_CHAT_ID is not set")

ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)


# -------------------------
# helpers: storage
# -------------------------

def save_user_contacts(user_id: int, real_name: str, phone_number: str):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="users!A2:F",
    ).execute()

    rows = result.get("values", [])
    target_row = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == str(user_id):
            target_row = idx
            break

    if not target_row:
        return False

    sheet.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"users!E{target_row}", "values": [[real_name]]},
                {"range": f"users!F{target_row}", "values": [[phone_number]]},
            ],
        },
    ).execute()

    return True


def pop_waiting_desc(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.pop("waiting_desc_for", None)

def _get_cart(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, int]:
    cart = context.user_data.get("cart")
    if not isinstance(cart, dict):
        cart = {}
        context.user_data["cart"] = cart
    return cart

def set_product_price(product_id: str, price: int):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="products!A2:A",
    ).execute()

    rows = result.get("values", [])
    row_index = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == product_id:
            row_index = idx
            break

    if row_index is None:
        return False

    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"products!C{row_index}",
        valueInputOption="RAW",
        body={"values": [[price]]},
    ).execute()

    return True

def pop_waiting_price(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.pop("waiting_price_for", None)

def _get_ui_msgs(context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    msgs = context.user_data.get("ui_msgs")
    if not isinstance(msgs, list):
        msgs = []
        context.user_data["ui_msgs"] = msgs
    return msgs

def _get_nav(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, str]:
    nav = context.user_data.get("nav")
    if not isinstance(nav, dict):
        nav = {}
        context.user_data["nav"] = nav
    return nav

def _fmt_money(krw: int) -> str:
    return f"{krw:,}₩"

def safe_open_photo(path: str):
    try:
        return open(path, "rb")
    except Exception:
        return None

def read_products_from_sheets() -> list[dict]:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="products!A2:G",
    ).execute()

    rows = result.get("values", [])
    products: list[dict] = []

    for row in rows:
        if len(row) < 5:
            continue

        products.append({
            "product_id": row[0],
            "name": row[1],
            "price": int(row[2]),
            "available": row[3].lower() == "true",
            "category": row[4],
            "photo_file_id": row[5] if len(row) > 5 else None,
            "description": row[6] if len(row) > 6 else None,
        })

    return products

import uuid
from datetime import datetime

def load_categories() -> list[str]:
    rows = read_products_from_sheets()
    return sorted({r["category"] for r in rows if r["available"]})

# -------------------------
# helpers: cart text
# -------------------------

from uuid import uuid4

def append_product_to_sheets(name: str, price: int, category: str, description: str) -> str | None:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    product_id = f"P{uuid4().hex[:10]}"

    row = [
        product_id,          # A
        name,                # B
        price,               # C
        "TRUE",              # D available
        category,            # E
        "",                  # F photo_file_id
        description or "",   # G
    ]

    try:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="products!A:G",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()
        return product_id
    except Exception:
        return None

def save_order_to_sheets(
    user,
    cart: dict,
    kind: str,
    comment: str,
    address: str | None = None,
) -> str | None:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    items = []
    total = 0

    for pid, qty in cart.items():
        p = get_product_by_id(pid)
        if not p:
            continue
        items.append(f"{p['name']} x{qty}")
        total += p["price"] * qty

    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    row = [[
        order_id,             # A order_id
        created_at,           # B created_at
        str(user.id),         # C user_id
        user.username or "",  # D username
        "; ".join(items),     # E items
        total,                # F total_price
        kind,                 # G type
        comment or "",        # H comment
        "",                   # I payment_proof (позже)
        "waiting_payment",    # J status
        "",                   # K handled_at
        "",                   # L handled_by
        "",                   # M reaction_seconds
        address or "",        # N address (NEW)
    ]]

    try:
        resp = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="orders!A:N",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()

        log.info(
            f"✅ ORDER APPENDED: order_id={order_id} "
            f"resp={resp.get('updates', {}).get('updatedRange')}"
        )
        return order_id

    except Exception:
        log.exception(f"❌ ORDER APPEND FAILED: buyer={user.id}")
        return None
    

def kb_staff_order(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"staff:approve:{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"staff:reject:{order_id}"),
        ]
    ])




def set_waiting_photo(context: ContextTypes.DEFAULT_TYPE, product_id: str):
    context.user_data["waiting_photo_for"] = product_id

def pop_waiting_photo(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.pop("waiting_photo_for", None)

def cart_total(cart: Dict[str, int]) -> int:
    total = 0
    for pid, qty in cart.items():
        p = get_product_by_id(pid)
        if p:
            total += p["price"] * qty
    return total

def cart_text(cart: Dict[str, int]) -> str:
    if not cart:
        return "Корзина пустая."

    lines: List[str] = []
    for pid, qty in cart.items():
        p = get_product_by_id(pid)
        if not p:
            continue
        lines.append(
            f"• {p['name']} × {qty} = {_fmt_money(p['price'] * qty)}"
        )

    lines.append("")
    lines.append(f"Итого: {_fmt_money(cart_total(cart))}")
    return "\n".join(lines)


# -------------------------
# "ONE WINDOW" UI: clear & track bot messages
# -------------------------
async def clear_ui(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
):
    """
    Удаляет все ранее отправленные ботом сообщения (которые мы трекаем).
    Всегда стараемся держать на экране только текущий "экран".
    """
    ids = _get_ui_msgs(context)
    if not ids:
        return

    # удаляем с конца (не принципиально, но аккуратно)
    for mid in reversed(ids):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

    ids.clear()

def track_msg(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    _get_ui_msgs(context).append(message_id)


# -------------------------
# keyboards
# -------------------------
def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💐 Каталог", callback_data="home:catalog")],
        [InlineKeyboardButton("🧺 Корзина", callback_data="home:cart")],
        [InlineKeyboardButton("ℹ️ Как заказать", callback_data="home:help")],
    ])

def kb_checkout_send() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Отправить заказ", callback_data="checkout:final_send")],
        [InlineKeyboardButton("❌ Отмена", callback_data="checkout:cancel")],
    ])


def kb_products(category: str) -> InlineKeyboardMarkup:
    products = read_products_from_sheets()

    rows = []
    for p in products:
        if not p["available"]:
            continue
        if p["category"] != category:
            continue

        rows.append([
            InlineKeyboardButton(
                f"{p['name']} — {_fmt_money(p['price'])}",
                callback_data=f"prod:{p['product_id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("⬅️ Категории", callback_data="nav:categories"),
        InlineKeyboardButton("🧺 Корзина", callback_data="nav:cart"),
    ])
    rows.append([InlineKeyboardButton("🏠 Домой", callback_data="nav:home")])

    return InlineKeyboardMarkup(rows)

def kb_product(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"cart:dec:{pid}"),
            InlineKeyboardButton("➕ Добавить", callback_data=f"cart:inc:{pid}"),
        ],
        [
            InlineKeyboardButton("🧺 Корзина", callback_data="nav:cart"),
            InlineKeyboardButton("⬅️ Назад", callback_data="nav:back"),
        ],
        [InlineKeyboardButton("🏠 Домой", callback_data="nav:home")],
    ])

def kb_cart(has_items: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_items:
        rows.append([InlineKeyboardButton("✅ Оформить", callback_data="checkout:start")])
        rows.append([InlineKeyboardButton("🧹 Очистить", callback_data="cart:clear")])
    rows.append([
        InlineKeyboardButton("💐 В каталог", callback_data="nav:catalog"),
        InlineKeyboardButton("🏠 Домой", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_checkout_pickup_delivery() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚶 Самовывоз", callback_data="checkout:type:pickup")],
        [InlineKeyboardButton("🛵 Доставка", callback_data="checkout:type:delivery")],
        [InlineKeyboardButton("↩️ Отмена", callback_data="checkout:cancel")],
    ])

def kb_checkout_preview():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 Прикрепить фото оплаты", callback_data="checkout:attach")],
        [InlineKeyboardButton("❌ Отмена", callback_data="checkout:cancel")],
    ])
# -------------------------
# menu button telegram
# -------------------------

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await clear_ui(context, chat_id)

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    context.user_data.clear()

    await clear_ui(context, chat_id)
    await render_home(context, chat_id)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Команды:\n"
        "/start — открыть прилавок\n"
        "/clear — очистить экран\n"
        "/restart — начать заново"
    )

# -------------------------
# render screens (always: clear -> send)
# -------------------------
def home_text() -> str:
    return (
        "🌸✨ <b>FlowerShopKR</b> ✨🌸\n\n"
        "Премиальные букеты и авторские композиции\n"
        "для особых моментов 💐\n\n"
        "🚚 Доставка и самовывоз\n"
        "🎁 Индивидуальная упаковка\n"
        "💌 Открытки и пожелания\n\n"
        "Выберите действие ниже ⬇️"
    )

async def render_home(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    nav = _get_nav(context)
    nav["screen"] = "home"
    await clear_ui(context, chat_id)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=home_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_home(),
    )
    track_msg(context, msg.message_id)

async def render_categories(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    nav = _get_nav(context)
    nav["screen"] = "categories"

    products = read_products_from_sheets()
    categories = get_categories_from_products(products)

    await clear_ui(context, chat_id)

    if not categories:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Каталог временно недоступен.",
        )
        track_msg(context, msg.message_id)
        return

    rows = [
        [InlineKeyboardButton(cat, callback_data=f"cat:{cat}")]
        for cat in categories
    ]
    rows.append([InlineKeyboardButton("🏠 Домой", callback_data="nav:home")])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    track_msg(context, msg.message_id)

async def on_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    product_id = context.user_data.get("waiting_photo_for")
    if not product_id:
        return  # фото не ждали

    photo = update.message.photo[-1]  # самое большое
    file_id = photo.file_id

    save_product_photo(product_id, file_id)

    context.user_data.pop("waiting_photo_for", None)

    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Фото привязано к товару.",
    )

    await catalog_cmd(update, context)

async def send_category_preview(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    category: str,
):
    """
    Превью категории: альбом из фото (если >=2),
    одно фото (если 1), иначе ничего.
    """
    items = [
        p for p in read_products_from_sheets()
        if p["category"] == category and p["available"]
    ]

    media: List[InputMediaPhoto] = []

    for p in items:
        if not p.get("photo_file_id"):
            continue

        media.append(
            InputMediaPhoto(
                media=p["photo_file_id"],
                caption=f"💐 <b>{p['name']}</b>\n{_fmt_money(p['price'])}",
                parse_mode=ParseMode.HTML,
            )
        )

    if len(media) >= 2:
        messages = await context.bot.send_media_group(
            chat_id=chat_id,
            media=media[:10],  # лимит Telegram
        )
        for m in messages:
            track_msg(context, m.message_id)

    elif len(media) == 1:
        m = await context.bot.send_photo(
            chat_id=chat_id,
            photo=media[0].media,
            caption=media[0].caption,
            parse_mode=ParseMode.HTML,
        )
        track_msg(context, m.message_id)


async def render_product_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int, pid: str):
    p = get_product_by_id(pid)
    if not p:
        await render_categories(context, chat_id)
        return

    nav = _get_nav(context)
    nav["screen"] = "product"
    nav["last_pid"] = pid

    cart = _get_cart(context)
    qty = cart.get(pid, 0)

    desc = p.get("description")
    desc_block = f"\n\n{desc}" if desc else ""

    text = (
        f"💐 <b>{p['name']}</b>\n"
        f"{desc_block}\n\n"
        f"Цена: <b>{_fmt_money(p['price'])}</b>\n"
        f"В корзине: <b>{qty}</b>"
    )

    await clear_ui(context, chat_id)

    if p.get("photo_file_id"):
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=p["photo_file_id"],
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_product(pid),
        )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_product(pid),
        )
        
    track_msg(context, msg.message_id)

async def render_cart(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    nav = _get_nav(context)
    nav["screen"] = "cart"
    cart = _get_cart(context)

    await clear_ui(context, chat_id)

    text = "🧺 <b>Корзина</b>\n\n" + cart_text(cart)
    m = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cart(bool(cart)),
    )
    track_msg(context, m.message_id)

async def render_help(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    nav = _get_nav(context)
    nav["screen"] = "help"

    await clear_ui(context, chat_id)

    text = (
        "ℹ️ <b>Как заказать</b>\n\n"
        "1) Откройте каталог\n"
        "2) Выберите букет/композицию и добавьте в корзину\n"
        "3) Оформите заказ (самовывоз/доставка)\n\n"
        "После отправки заказа мы свяжемся для подтверждения.\n\n"
        f"Контакт: {SHOP_PHONE}"
    )
    m = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_home(),
    )
    track_msg(context, m.message_id)

async def render_product_list(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    category: str,
):
    nav = _get_nav(context)
    nav["screen"] = "product_list"
    nav["last_category"] = category

    await clear_ui(context, chat_id)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"📦 <b>{category}</b>\nВыберите позицию:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_products(category),
    )
    track_msg(context, msg.message_id)

# -------------------------
# /start
# -------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user_if_new(user)

    chat_id = update.effective_chat.id
    await render_home(context, chat_id)

async def dash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != OWNER_CHAT_ID_INT:
        return

    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:N",
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📊 Дашборд\n\nЗаказов пока нет.",
        )
        return

    now = datetime.utcnow()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    revenue_today = 0
    revenue_week = 0
    revenue_month = 0

    pending = approved = rejected = 0
    reaction_times = []

    for row in rows[1:]:
        try:
            created_at = datetime.fromisoformat(row[1])
            total = int(row[5])
            status = row[9]
            reaction_seconds = row[12] if len(row) > 12 else ""

        except Exception:
            continue

        if created_at.date() == today:
            revenue_today += total

        if created_at >= week_ago:
            revenue_week += total

        if created_at >= month_start:
            revenue_month += total

        if status == "pending":
            pending += 1
        elif status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1

        if reaction_seconds:
            try:
                reaction_times.append(int(reaction_seconds))
            except Exception:
                pass

    avg_reaction_min = (
        sum(reaction_times) / len(reaction_times) / 60
        if reaction_times else 0
    )

    text = (
        "📊 <b>Дашборд владельца</b>\n\n"
        "💰 <b>Выручка</b>\n"
        f"• Сегодня: <b>{_fmt_money(revenue_today)}</b>\n"
        f"• За 7 дней: <b>{_fmt_money(revenue_week)}</b>\n"
        f"• За месяц: <b>{_fmt_money(revenue_month)}</b>\n\n"
        "📦 <b>Статусы заказов</b>\n"
        f"• В ожидании: <b>{pending}</b>\n"
        f"• Приняты: <b>{approved}</b>\n"
        f"• Отклонены: <b>{rejected}</b>\n\n"
        "⏱ <b>Среднее время реакции</b>\n"
        f"• {avg_reaction_min:.1f} мин"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
    )

# -------------------------
# чекаут
# -------------------------

async def on_checkout_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    chat_id = msg.chat_id
    text = (msg.text or "").strip()
    step = context.user_data.get("checkout_step")

    # --- ЭТАП 1: ИМЯ ---
    if step == "ask_name":
        if not text:
            await msg.reply_text("❌ Пожалуйста, введите имя.")
            return

        checkout = context.user_data.setdefault("checkout", {})
        checkout["real_name"] = text
        context.user_data["checkout_step"] = "ask_phone"

        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📞 <b>Ваш номер телефона</b>\n\n"
                "Введите номер для связи ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True),
        )
        track_msg(context, m.message_id)
        return

    # --- ЭТАП 2: ТЕЛЕФОН ---
    if step == "ask_phone":
        if not text:
            await msg.reply_text("❌ Пожалуйста, введите номер телефона.")
            return

        checkout = context.user_data.setdefault("checkout", {})
        checkout["phone_number"] = text

        save_user_contacts(
            user_id=msg.from_user.id,
            real_name=checkout.get("real_name"),
            phone_number=text,
        )

        context.user_data["checkout_step"] = "type"

        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text="🚚 <b>Выберите способ получения:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_checkout_pickup_delivery(),
        )
        track_msg(context, m.message_id)
        return

    # --- ЭТАП 2.5: АДРЕС (ТОЛЬКО ДЛЯ ДОСТАВКИ) ---
    if step == "ask_address":
        if not text:
            await msg.reply_text("❌ Пожалуйста, введите адрес на корейском.")
            return

        checkout = context.user_data.setdefault("checkout", {})
        checkout["address"] = text

        context.user_data["checkout_step"] = "comment"

        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✍️ Напишите комментарий к заказу.\n\n"
                "• Например: удобное время доставки\n\n"
                "⬇️ Ответьте на это сообщение"
            ),
            reply_markup=ForceReply(selective=True),
        )
        track_msg(context, m.message_id)
        return

    # --- ЭТАП 3: КОММЕНТАРИЙ ---
    if step != "comment":
        return

    if not text:
        await msg.reply_text("✍️ Напишите комментарий или '-'")
        return

    checkout = context.user_data.setdefault("checkout", {})
    checkout["comment"] = text
    context.user_data["checkout_step"] = "preview"

    cart = _get_cart(context)
    kind = checkout.get("type", "pickup")
    kind_label = "Самовывоз" if kind == "pickup" else "Доставка"

    preview_text = build_checkout_preview(
        cart=cart,
        kind_label=kind_label,
        comment=text,
        address=checkout.get("address"),
    )

    await clear_ui(context, chat_id)
    m = await context.bot.send_message(
        chat_id=chat_id,
        text=preview_text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_checkout_preview(),
    )
    track_msg(context, m.message_id)

# -------------------------
# main router (callbacks)
# -------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q is None:
        return

    data = q.data or ""
    log.info(f"CALLBACK DATA = {data}")

    await q.answer()
    chat_id = q.message.chat_id
    nav = _get_nav(context)

    # ---------- NAV ----------
    if data == "nav:home":
        await render_home(context, chat_id)
        return

    if data in ("home:catalog", "nav:catalog", "nav:categories"):
        await render_categories(context, chat_id)
        return

    if data in ("home:cart", "nav:cart"):
        await render_cart(context, chat_id)
        return

    if data == "home:help":
        await render_help(context, chat_id)
        return

    if data == "nav:back":
        screen = nav.get("screen", "home")
        if screen == "product":
            last_cat = nav.get("last_category")
            if last_cat:
                await render_product_list(context, chat_id, last_cat)
            else:
                await render_categories(context, chat_id)
        elif screen == "product_list":
            await render_categories(context, chat_id)
        else:
            await render_home(context, chat_id)
        return

    # ---------- CATEGORIES / PRODUCTS ----------
    if data.startswith("cat:"):
        await render_product_list(context, chat_id, data.split(":", 1)[1])
        return

    if data.startswith("prod:"):
        await render_product_card(context, chat_id, data.split(":", 1)[1])
        return

    # ---------- CART ----------
    if data.startswith("cart:inc:"):
        pid = data.split(":")[-1]
        cart = _get_cart(context)
        cart[pid] = cart.get(pid, 0) + 1
        await render_product_card(context, chat_id, pid)
        return

    if data.startswith("cart:dec:"):
        pid = data.split(":")[-1]
        cart = _get_cart(context)
        if pid in cart:
            cart[pid] -= 1
            if cart[pid] <= 0:
                del cart[pid]
        await render_product_card(context, chat_id, pid)
        return

    if data == "cart:clear":
        context.user_data["cart"] = {}
        await render_cart(context, chat_id)
        return

    # ---------- CHECKOUT ----------

    if data == "checkout:final_send":
        checkout = context.user_data.get("checkout")
        if not checkout:
            return

        # 1) строгая проверка шага
        if not checkout.get("payment_photo_file_id"):
            log.warning("⛔ final_send ignored: no payment photo")
            return

        # 2) обязательные данные
        payment_file_id = checkout.get("payment_photo_file_id")
        if not payment_file_id:
            log.warning("⛔ final_send ignored: no payment photo")
            return

        cart = _get_cart(context)
        if not cart:
            log.warning("⛔ final_send ignored: empty cart")
            return

        kind = checkout.get("type", "pickup")
        kind_label = "Самовывоз" if kind == "pickup" else "Доставка"
        comment = checkout.get("comment", "")

        user = q.from_user

        # 3) создаем заказ
        order_id = save_order_to_sheets(
            user=user,
            cart=cart,
            kind=kind_label,
            comment=comment,
            address=checkout.get("address"),
        )
        if not order_id:
            await clear_ui(context, chat_id)
            m = await context.bot.send_message(
                chat_id=chat_id,
                text="❗ Не удалось отправить заказ. Попробуйте еще раз.",
                reply_markup=kb_home(),
            )
            track_msg(context, m.message_id)
            return

        # 4) сохраняем payment_proof + статус pending
        service = get_sheets_service()
        sheet = service.spreadsheets()

        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="orders!A:J",
        ).execute()
        rows = result.get("values", [])

        target_row = None
        for idx, row in enumerate(rows, start=1):
            if row and row[0] == order_id:
                target_row = idx
                break

        if target_row:
            sheet.values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={
                    "valueInputOption": "RAW",
                    "data": [
                        {"range": f"orders!I{target_row}", "values": [[payment_file_id]]},
                        {"range": f"orders!J{target_row}", "values": [["pending"]]},
                    ],
                },
            ).execute()

        # 5) уведомляем сотрудника ОДИН РАЗ
        await notify_staff(
            context,
            order_id,
        )

        # 6) чистим state
        context.user_data.pop("checkout", None)
        context.user_data.pop("checkout_step", None)
        context.user_data["cart"] = {}

        # 7) финал покупателю
        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ <b>Заказ отправлен</b>\n\n"
                "Мы получили оплату и передали заказ в обработку.\n"
                "Скоро свяжемся с вами 💐"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_home(),
        )
        track_msg(context, m.message_id)
        return

    if data == "checkout:start":
        if not _get_cart(context):
            await render_cart(context, chat_id)
            return

        context.user_data["checkout"] = {}
        context.user_data["checkout_step"] = "ask_name"

        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✍️ <b>Как вас зовут?</b>\n\n"
                "Введите ваше имя и фамилию ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True),
        )
        track_msg(context, m.message_id)
        return

    if data.startswith("checkout:type:"):
        kind = data.split(":")[-1]

        checkout = context.user_data.setdefault("checkout", {})
        checkout["type"] = kind

        await clear_ui(context, chat_id)

        # 🚚 ДОСТАВКА → СПРАШИВАЕМ АДРЕС
        if kind == "delivery":
            context.user_data["checkout_step"] = "ask_address"

            m = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📍 <b>Укажите адрес доставки</b>\n\n"
                    "Введите адрес <b>на корейском языке</b>.\n"
                    "Это нужно для правильной навигации курьера ⬇️"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True),
            )
            track_msg(context, m.message_id)
            return

        # 🚶 САМОВЫВОЗ → СРАЗУ К КОММЕНТАРИЮ
        context.user_data["checkout_step"] = "comment"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✍️ Напишите комментарий к заказу.\n\n"
                "• Укажите удобное время самовывоза\n\n"
                "⬇️ Ответьте на это сообщение"
            ),
            reply_markup=ForceReply(selective=True),
        )
        track_msg(context, m.message_id)
        return
    
    if data == "checkout:attach":
        checkout = context.user_data.get("checkout")
        if not checkout:
            return

        await clear_ui(context, chat_id)

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📎 <b>Прикрепите фото оплаты</b>\n\n"
                "Фото нужно отправить <b>ответом на это сообщение</b>.\n"
                "Нажмите 📎 внизу экрана ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True),
        )

        context.user_data["checkout_step"] = "wait_photo"
        checkout["photo_reply_to"] = m.message_id
        track_msg(context, m.message_id)
        return


    if data == "checkout:cancel":
        context.user_data.pop("checkout", None)
        context.user_data.pop("checkout_step", None)
        await render_cart(context, chat_id)
        return

    
async def on_buyer_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("📸 BUYER PAYMENT PHOTO HANDLER FIRED")
    msg = update.message
    if not msg or not msg.photo or not msg.reply_to_message:
        return

    chat_id = msg.chat_id
    if chat_id in STAFF_CHAT_IDS:
        return

    checkout = context.user_data.get("checkout")
    if not checkout:
        return

    if context.user_data.get("checkout_step") != "wait_photo":
        return

    expected_reply_to = checkout.get("photo_reply_to")
    if msg.reply_to_message.message_id != expected_reply_to:
        return

    # берем самое большое фото
    file_id = msg.photo[-1].file_id
    checkout["payment_photo_file_id"] = file_id

    # показываем подтверждение + кнопку отправки
    cart = _get_cart(context)
    kind = checkout.get("type", "pickup")
    kind_label = "Самовывоз" if kind == "pickup" else "Доставка"
    comment = checkout.get("comment", "")

    preview_text = build_checkout_preview(
        cart=cart,
        kind_label=kind_label,
        comment=comment,
        address=checkout.get("address"),
    )

    await clear_ui(context, chat_id)
    m = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ <b>Фото получено</b>\n\n"
            "Теперь вы можете отправить заказ ⬇️\n\n"
            + preview_text
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_checkout_send(),  # сделаем на следующем шаге
    )
    track_msg(context, m.message_id)

    context.user_data["checkout_step"] = "ready_to_send"

async def on_staff_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()
    chat_id = q.message.chat_id

    if chat_id not in STAFF_CHAT_IDS:
        return

    data = q.data or ""
    try:
        _, action, order_id = data.split(":", 2)
        log.info(f"🧾 STAFF ACTION: {action} on order {order_id}")


    except ValueError:
        log.warning(f"⚠️ invalid callback data: {data}")
        return

    service = get_sheets_service()
    sheet = service.spreadsheets()

    # --- читаем заказы ---
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:N",  # ⬅️ до reaction_seconds
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        log.warning("⚠️ orders sheet empty")
        return

    data_rows = rows[1:]

    target_row = None
    target_index = None

    for idx, row in enumerate(data_rows, start=2):
        if row and row[0] == order_id:
            target_row = row
            target_index = idx
            break

    if not target_row:
        log.warning(f"⚠️ order {order_id} not found")
        return

    current_status = target_row[9] if len(target_row) > 9 else ""
    if current_status != "pending":
        log.info(
            f"⛔ order {order_id} already handled "
            f"(status={current_status})"
        )
        try:
            await q.answer("Заказ уже обработан", show_alert=True)
        except Exception:
            pass
        return

    buyer_chat_id = int(target_row[2])

    # --- действие ---
    if action == "approve":
        new_status = "approved"
        buyer_text = "💐 Ваш заказ принят в работу!"
    elif action == "reject":
        new_status = "rejected"
        buyer_text = "❗ Мы уточним детали заказа и свяжемся с вами."
    else:
        return

    # --- метрика времени реакции ---
    try:
        created_at = datetime.fromisoformat(target_row[1])
        handled_at = datetime.utcnow()
        reaction_seconds = int((handled_at - created_at).total_seconds())
    except Exception as e:
        log.warning(f"⚠️ reaction time calc failed: {e}")
        handled_at = datetime.utcnow()
        reaction_seconds = ""

    # --- batch update ---
    sheet.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {
                    "range": f"orders!J{target_index}",
                    "values": [[new_status]],
                },
                {
                    "range": f"orders!K{target_index}",
                    "values": [[handled_at.isoformat()]],
                },
                {
                    "range": f"orders!L{target_index}",
                    "values": [[str(chat_id)]],
                },
                {
                    "range": f"orders!M{target_index}",
                    "values": [[reaction_seconds]],
                },
            ],
        },
    ).execute()

    log.info(
        f"🧾 order {target_row[0]} {new_status} "
        f"by staff={chat_id}, reaction={reaction_seconds}s"
    )

    # --- сообщение покупателю ---
    await context.bot.send_message(
        chat_id=buyer_chat_id,
        text=buyer_text,
    )

    # --- фидбек сотруднику ---
    try:
        await q.edit_message_caption(
            caption=(
                q.message.caption
                + f"\n\n<b>Статус:</b> {new_status.upper()}"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception as e:
        log.warning(f"edit_message_caption failed: {e}")


async def on_catalog_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return

    await q.answer()

    chat_id = q.message.chat_id
    data = q.data or ""

    if chat_id not in STAFF_CHAT_IDS:
        return

    # --- NAV внутри staff-каталога ---
    if data == "catalog:back":
        await render_catalog_categories(context, chat_id)
        return

    if data.startswith("catalog:cat:"):
        category = data.split(":", 2)[2]
        await render_catalog_products(context, chat_id, category)
        return

    # --- действия по товару / добавление ---
    parts = data.split(":")
    if len(parts) < 3:
        return

    action = parts[1]
    product_id = parts[2]

    if action == "add":
        context.user_data["waiting_add_name"] = True
        await context.bot.send_message(
            chat_id=chat_id,
            text="➕ Добавление товара\n\nВведите название товара:",
        )
        return

    if action == "desc":
        context.user_data["waiting_desc_for"] = product_id
        await context.bot.send_message(chat_id=chat_id, text="📝 Введите описание товара:")
        return

    if action == "price":
        context.user_data["waiting_price_for"] = product_id
        await context.bot.send_message(chat_id=chat_id, text="✏️ Введите новую цену (только число, в вонах):")
        return

    if action == "photo":
        set_waiting_photo(context, product_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📷 Отправьте фото для товара.\n\n"
                "Можно отправить одно фото.\n"
                "Оно будет привязано к позиции."
            ),
        )
        return

    if action == "toggle":
        products = read_products_from_sheets()
        product = next((p for p in products if p["product_id"] == product_id), None)
        if not product:
            return
        set_product_available(product_id, not product["available"])
        # остаемся в той же категории, если она сохранена
        current_cat = context.user_data.get("catalog_category")
        if current_cat:
            await render_catalog_products(context, chat_id, current_cat)
        else:
            await catalog_cmd(update, context)
        return

# 1️⃣ ЕСЛИ ЭТО ФОТО — НИЧЕГО НЕ ПЕРЕКЛЮЧАЕМ
    if action == "photo":
        set_waiting_photo(context, product_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📷 Отправьте фото для товара.\n\n"
                "Можно отправить одно фото.\n"
                "Оно будет привязано к позиции."
            ),
        )
        return

    # 2️⃣ ИНАЧЕ — это toggle
    if action == "toggle":
        set_product_available(product_id, not product["available"])
        await catalog_cmd(update, context)
        return

SHOP_NAME = "Цветочный бутик"
SHOP_PHONE = "010-1234-5678"
SHOP_NOTE = "Свежие цветы. Индивидуальная упаковка."


# -------------------------
# checkout conversation
# -------------------------
CHECKOUT_TYPE, CHECKOUT_COMMENT, CHECKOUT_CONFIRM = range(3)

async def on_staff_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in STAFF_CHAT_IDS:
        return

    product_id = pop_waiting_photo(context)
    if not product_id:
        return

    if not update.message:
        return

    file_id = None

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type.startswith("image/"):
        file_id = update.message.document.file_id

    if not file_id:
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    set_product_photo(product_id, file_id)

    await update.message.reply_text("✅ Фото сохранено.")
    await catalog_cmd(update, context)


async def on_staff_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in STAFF_CHAT_IDS:
        return

    text = (update.message.text or "").strip()

    # ===== ДОБАВЛЕНИЕ ТОВАРА =====

    if context.user_data.get("waiting_add_name"):
        if not text:
            await update.message.reply_text("❌ Название не может быть пустым. Введите название товара:")
            return

        context.user_data.pop("waiting_add_name", None)
        context.user_data["adding_product"] = {"name": text}
        context.user_data["waiting_add_price"] = True
        await update.message.reply_text("Введите цену (только число, в вонах):")
        return

    if context.user_data.get("waiting_add_price"):
        if not text.isdigit():
            await update.message.reply_text("❌ Цена должна быть числом. Введите цену в вонах:")
            return

        price = int(text)
        if price <= 0:
            await update.message.reply_text("❌ Цена должна быть больше нуля. Введите цену в вонах:")
            return

        context.user_data.pop("waiting_add_price", None)
        adding = context.user_data.get("adding_product") or {}
        adding["price"] = price
        context.user_data["adding_product"] = adding

        context.user_data["waiting_add_category"] = True
        await update.message.reply_text("Введите категорию (как хотите видеть у покупателя):")
        return

    if context.user_data.get("waiting_add_category"):
        if not text:
            await update.message.reply_text("❌ Категория не может быть пустой. Введите категорию:")
            return

        context.user_data.pop("waiting_add_category", None)
        adding = context.user_data.get("adding_product") or {}
        adding["category"] = text
        context.user_data["adding_product"] = adding

        context.user_data["waiting_add_desc"] = True
        await update.message.reply_text("Введите описание товара или отправьте '-' чтобы пропустить:")
        return

    if context.user_data.get("waiting_add_desc"):
        context.user_data.pop("waiting_add_desc", None)

        desc = "" if text == "-" else text
        adding = context.user_data.pop("adding_product", {})

        new_pid = append_product_to_sheets(
            name=adding.get("name", ""),
            price=int(adding.get("price", 0)),
            category=adding.get("category", ""),
            description=desc,
        )

        if new_pid:
            await update.message.reply_text("✅ Товар добавлен. Фото можно привязать кнопкой '🖼 Фото' в /catalog.")
        else:
            await update.message.reply_text("❌ Не удалось добавить товар в Google Sheets.")

        await catalog_cmd(update, context)
        return

    # ===== РЕДАКТИРОВАНИЕ ЦЕНЫ =====

    product_id = context.user_data.get("waiting_price_for")
    if product_id:
        if not text.isdigit():
            await update.message.reply_text("❌ Цена должна быть числом.")
            return

        price = int(text)
        if price <= 0:
            await update.message.reply_text("❌ Цена должна быть больше нуля.")
            return

        context.user_data.pop("waiting_price_for", None)
        set_product_price(product_id, price)
        await update.message.reply_text("✅ Цена обновлена.")
        await catalog_cmd(update, context)
        return

    # ===== РЕДАКТИРОВАНИЕ ОПИСАНИЯ =====

    product_id = context.user_data.get("waiting_desc_for")
    if product_id:
        if not text:
            await update.message.reply_text("❌ Описание не может быть пустым.")
            return

        context.user_data.pop("waiting_desc_for", None)
        set_product_description(product_id, text)
        await update.message.reply_text("✅ Описание сохранено.")
        await catalog_cmd(update, context)
        return

async def on_staff_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in STAFF_CHAT_IDS:
        return

    product_id = pop_waiting_price(context)
    if not product_id:
        return

    text = (update.message.text or "").strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Цена должна быть числом. Попробуйте еще раз.")
        context.user_data["waiting_price_for"] = product_id
        return

    price = int(text)
    if price <= 0:
        await update.message.reply_text("❌ Цена должна быть больше нуля.")
        context.user_data["waiting_price_for"] = product_id
        return

    set_product_price(product_id, price)

    await update.message.reply_text("✅ Цена обновлена.")
    await catalog_cmd(update, context)

async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id

    cart = _get_cart(context)
    if not cart:
        await render_cart(context, chat_id)
        return ConversationHandler.END

    context.user_data["checkout"] = {}

    await clear_ui(context, chat_id)
    m = await context.bot.send_message(
        chat_id=chat_id,
        text="✅ <b>Оформление заказа</b>\n\nВыберите способ получения:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_checkout_pickup_delivery(),
    )
    track_msg(context, m.message_id)
    return CHECKOUT_TYPE




async def on_staff_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in STAFF_CHAT_IDS:
        return

    product_id = pop_waiting_desc(context)
    if not product_id:
        return

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Описание не может быть пустым.")
        context.user_data["waiting_desc_for"] = product_id
        return

    set_product_description(product_id, text)

    await update.message.reply_text("✅ Описание сохранено.")
    await catalog_cmd(update, context)



# -------------------------
# main/helpers
# -------------------------

def set_product_description(product_id: str, description: str):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="products!A2:A",
    ).execute()

    rows = result.get("values", [])
    row_index = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == product_id:
            row_index = idx
            break

    if row_index is None:
        return False

    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"products!G{row_index}",
        valueInputOption="RAW",
        body={"values": [[description]]},
    ).execute()

    return True

def register_user_if_new(user):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="users!A2:A",
    ).execute()

    rows = result.get("values", [])
    existing_ids = {row[0] for row in rows if row}

    if str(user.id) in existing_ids:
        return False

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="users!A:D",
        valueInputOption="RAW",
        body={
            "values": [[
                str(user.id),
                user.username or "",
                user.full_name or "",
                datetime.utcnow().isoformat(),
            ]]
        },
    ).execute()

    return True

def get_sheets_service():
    creds = Credentials.from_service_account_info(
        GOOGLE_CREDS_INFO,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)



def set_waiting_photo(context: ContextTypes.DEFAULT_TYPE, product_id: str):
    context.user_data["waiting_photo_for"] = product_id

def set_product_available(product_id: str, available: bool):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="products!A2:A",
    ).execute()

    rows = result.get("values", [])
    row_index = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == product_id:
            row_index = idx
            break

    if row_index is None:
        return False

    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"products!D{row_index}",
        valueInputOption="RAW",
        body={"values": [["TRUE" if available else "FALSE"]]},
    ).execute()

    return True

def set_product_photo(product_id: str, file_id: str):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="products!A2:A",
    ).execute()

    rows = result.get("values", [])
    row_index = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == product_id:
            row_index = idx
            break

    if row_index is None:
        return False

    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"products!F{row_index}",  # ВОТ ТУТ F
        valueInputOption="RAW",
        body={"values": [[file_id]]},
    ).execute()

    return True


def kb_catalog_item(product_id: str, available: bool) -> InlineKeyboardMarkup:
    label = "🙈 Скрыть" if available else "👁 Показать"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(label, callback_data=f"catalog:toggle:{product_id}"),
            InlineKeyboardButton("✏️ Цена", callback_data=f"catalog:price:{product_id}"),
            InlineKeyboardButton("📝 Описание", callback_data=f"catalog:desc:{product_id}"),
            InlineKeyboardButton("🖼 Фото", callback_data=f"catalog:photo:{product_id}"),
        ]
    ])

def kb_catalog_controls() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="catalog:add:0")]
    ])

async def render_catalog_categories(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    products = read_products_from_sheets()
    categories = sorted({
        p["category"] for p in products if p.get("category")
    })

    await clear_ui(context, chat_id)

    if not categories:
        m = await context.bot.send_message(
            chat_id=chat_id,
            text="Категорий нет.",
        )
        track_msg(context, m.message_id)
        return

    rows = [
        [InlineKeyboardButton(cat, callback_data=f"catalog:cat:{cat}")]
        for cat in categories
    ]

    m = await context.bot.send_message(
        chat_id=chat_id,
        text="🛠 <b>Каталог</b>\nВыберите категорию:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )
    track_msg(context, m.message_id)


async def catalog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in STAFF_CHAT_IDS:
        return

    products = read_products_from_sheets()
    categories = sorted({
        p["category"]
        for p in products
        if p.get("category")
    })

    await clear_ui(context, chat_id)

    header = await context.bot.send_message(
        chat_id=chat_id,
        text="🛠 <b>Управление каталогом</b>\n\nВыберите категорию:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_catalog_controls(),
    )
    track_msg(context, header.message_id)

    if not categories:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Категорий пока нет.",
        )
        track_msg(context, msg.message_id)
        return

    for cat in categories:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📦 <b>{cat}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Открыть", callback_data=f"catalog:cat:{cat}")]
            ]),
        )
        track_msg(context, msg.message_id)

async def render_catalog_products(
    
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    category: str,
):
    products = [
        p for p in read_products_from_sheets()
        if p.get("category") == category
    ]
    context.user_data["catalog_category"] = category
    await clear_ui(context, chat_id)

    header = await context.bot.send_message(
        chat_id=chat_id,
        text=f"🛠 <b>{category}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Категории", callback_data="catalog:back")]
        ]),
    )
    track_msg(context, header.message_id)

    if not products:
        m = await context.bot.send_message(
            chat_id=chat_id,
            text="В этой категории нет товаров.",
        )
        track_msg(context, m.message_id)
        return

    for i, p in enumerate(products, start=1):
        status = "доступен" if p["available"] else "скрыт"
        text = (
            f"{i}. <b>{p['name']}</b>\n"
            f"Цена: {_fmt_money(p['price'])}\n"
            f"Статус: {status}"
        )

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_catalog_item(
                p["product_id"],
                p["available"],
            ),
        )
        track_msg(context, m.message_id)

async def notify_staff(context: ContextTypes.DEFAULT_TYPE, order_id: str):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # --- читаем заказы ---
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:N",
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        return

    target = None
    for row in rows[1:]:
        if row and row[0] == order_id:
            target = row
            break

    if not target:
        return

    (
        _order_id,        # A
        created_at,       # B
        buyer_chat_id,    # C
        buyer_username,   # D
        items,            # E
        total,            # F
        kind,             # G
        comment,          # H
        payment_file_id,  # I
        status,           # J
        *_rest,
    ) = target + [""] * 10

    # колонка N (address)
    address = target[13] if len(target) > 13 else ""

    if status != "pending":
        return

    # --- вытаскиваем имя / телефон из users ---
    buyer_name = ""
    buyer_phone = ""

    users = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="users!A:F",
    ).execute().get("values", [])

    for u in users:
        if u and u[0] == buyer_chat_id:
            buyer_name = u[4] if len(u) > 4 else ""
            buyer_phone = u[5] if len(u) > 5 else ""
            break

    address_block = (
        f"\n📍 <b>Адрес:</b>\n<code>{address}</code>\n"
        if address else ""
    )

    caption = (
        "🛎 <b>Новый заказ</b>\n\n"
        f"🧾 ID: <code>{order_id}</code>\n\n"
        f"👤 <b>Имя:</b> {buyer_name or '—'}\n"
        f"📞 <b>Телефон:</b> <code>{buyer_phone or '—'}</code>\n"
        f"{address_block}\n"
        f"{items}\n\n"
        f"💰 Итого: <b>{_fmt_money(int(total))}</b>\n"
        f"🚚 Способ: <b>{kind}</b>\n"
        f"💬 Комментарий: <b>{comment or '—'}</b>"
    )

    for staff_id in STAFF_CHAT_IDS:
        try:
            await context.bot.send_photo(
                chat_id=staff_id,
                photo=payment_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_staff_order(order_id),
            )
        except Exception as e:
            log.warning(f"⚠️ notify_staff failed for {staff_id}: {e}")



async def on_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("checkout_step"):
        return  # ❗ не мешаем checkout FSM

    if not update.message:
        return

    chat_id = update.effective_chat.id
    if chat_id in STAFF_CHAT_IDS:
        await on_staff_text(update, context)

def build_checkout_preview(
    cart: dict,
    kind_label: str,
    comment: str,
    address: str | None = None,
) -> str:
    address_block = (
        f"Адрес: <b>{address}</b>\n"
        if address else ""
    )

    return (
        "🧾 <b>Проверьте заказ</b>\n\n"
        f"{cart_text(cart)}\n\n"
        f"Способ: <b>{kind_label}</b>\n"
        f"{address_block}"
        f"Комментарий: <b>{comment or '—'}</b>\n\n"
        "Чтобы отправить заказ, прикрепите фото оплаты ⬇️"
    )

def main():
    
    
    app = Application.builder().token(BOT_TOKEN).build()
    # -------- COMMANDS --------
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("help", help_cmd))  # ← ВОТ ЭТОГО НЕ ХВАТАЛО
    app.add_handler(CommandHandler("catalog", catalog_cmd))
    app.add_handler(CommandHandler("dash", dash_cmd))

    # -------- CALLBACKS (ВСЕ КНОПКИ) --------
    
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE)
            & ~filters.Chat(STAFF_CHAT_IDS),
            on_buyer_payment_photo
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            on_button,
            pattern=r"^(home:|nav:|cat:|prod:|cart:|checkout:)"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            on_catalog_toggle,
            pattern=r"^catalog:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            on_staff_decision,
            pattern=r"^staff:(approve|reject):"
        )
    )

    async def debug_any_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        log.info("🟥 DEBUG: PHOTO UPDATE ARRIVED")

    
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY & ~filters.PHOTO & ~filters.Document.ALL,
            on_checkout_reply
        )
    )

    # -------- STAFF --------
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.Chat(STAFF_CHAT_IDS),
            on_staff_photo
        )
    )
    
# -------- BUYER PHOTO (payment proof) --------
    
    log.info("Bot started")
    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
        ],
        drop_pending_updates=True,
    )

def get_product_by_id(pid: str) -> dict | None:
    for p in read_products_from_sheets():
        if p["product_id"] == pid and p["available"]:
            return p
    return None

def get_categories_from_products(products: list[dict]) -> list[str]:
    return sorted({
        p["category"]
        for p in products
        if p["available"] and p.get("category")
    })



if __name__ == "__main__":
    main()