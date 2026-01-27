# main.py — BARAKAT PROD "электронный прилавок"
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
#   
# IMPORTANT:
# ForceReply messages must be handled via filters.REPLY
# filters.TEXT is unreliable after callbacks + deleteMessage


import os
import logging
from typing import Dict, List, Optional
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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
from sheets_repo import get_sheets_service
from google.oauth2 import service_account
from googleapiclient.discovery import build
from broadcast import register_broadcast_handlers
from dotenv import load_dotenv
load_dotenv()

from telegram.ext import CallbackQueryHandler
from staff_callbacks import staff_callback

from config import (
    BOT_TOKEN,
    OWNER_CHAT_ID_INT,
    ADMIN_CHAT_ID_INT,
    STAFF_CHAT_IDS,
    SPREADSHEET_ID,
)
HOME_PHOTO_FILE_ID = "AgACAgUAAxkBAAIBWml2tkzPZ3lgBPKTVeeA3Wi9Z3yJAAKuDWsbhLi4VyKeP_hEUISAAQADAgADeQADOAQ"
# -------------------------
# logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("FlowerShopKR")




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
# web api patch - note - delete
# -------------------------

from types import SimpleNamespace
from telegram import Bot

_bot_instance = Bot(token=BOT_TOKEN)

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
    subtotal = 0

    for pid, qty in cart.items():
        p = get_product_by_id(pid)
        if not p:
            continue
        items.append(f"{p['name']} x{qty}")
        subtotal += p["price"] * qty

    # доставка
    delivery_fee = 0
    if kind == "Доставка":
        if subtotal < FREE_DELIVERY_FROM:
            delivery_fee = DELIVERY_FEE

    total = subtotal + delivery_fee

    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    row = [[
        order_id,
        created_at,
        str(user.id),
        user.username or "",
        "; ".join(items),
        total,
        kind,
        comment or "",
        "",
        "created",          # ← статус
        "",
        "",
        "",
        address or "",
        delivery_fee,
        "stub",             # ← delivery_fee_source (новая колонка P)
    ]]

    try:
        resp = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="orders!A:O",
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

def kb_staff_pickup_eta(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10 мин", callback_data=f"staff:eta:10:{order_id}"),
            InlineKeyboardButton("20 мин", callback_data=f"staff:eta:20:{order_id}"),
            InlineKeyboardButton("30 мин", callback_data=f"staff:eta:30:{order_id}"),
        ],
        [
            InlineKeyboardButton("45 мин", callback_data=f"staff:eta:45:{order_id}"),
            InlineKeyboardButton("60 мин", callback_data=f"staff:eta:60:{order_id}"),
        ],
        [
            InlineKeyboardButton("🕒 Указать дату и время", callback_data=f"staff:eta_manual:{order_id}"),
        ],
        [
            InlineKeyboardButton("❌ Не вызывать курьера", callback_data=f"staff:no_courier:{order_id}"),
        ],
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

def calc_delivery_fee(cart: dict, kind: str) -> int:
    if kind != "delivery":
        return 0

    # временно используем Web API stub
    result = webapi_calculate_delivery(cart, address=None)
    return int(result.get("price", 0))

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
        [InlineKeyboardButton("🥘 Каталог", callback_data="home:catalog")],
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
        InlineKeyboardButton("🥘 В каталог", callback_data="nav:catalog"),
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
        [InlineKeyboardButton("📎 Прикрепить скриншот", callback_data="checkout:attach")],
        [InlineKeyboardButton("❌ Отмена", callback_data="checkout:cancel")],
    ])

def kb_retry_courier(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Повторить отправку курьеру", callback_data=f"staff:courier_retry:{order_id}")]
    ])

def kb_owner_paid():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Оплачено", callback_data="owner:commission_paid")]
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
        "РАДУГА ДУНПО 🌈\n"
        "Магазин русских товаров и домашней выпечки\n\n"
        "📍🚚 Доставка по Дунпо 4.000 ₩ .\n"
        "🆓 Бесплатно от 50.000 ₩.\n"
        "🥘 📞 Для справок: 010-XXXX-XXXX\n"
        
        "💳 Оплата переводом на счет магазина\n\n"
        "Всегда начинайте Ваш заказ написав команду /start прямо в чат Telegram.\n\n"        
        "👇\n"
        "ЧТОБЫ СДЕЛАТЬ ЗАКАЗ\n\n"
        "⬇️НАЖМИТЕ КНОПКУ WebApp⬇️\n"
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
        "2) Выберите блюдо и добавьте в корзину\n"
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

    if chat_id == ADMIN_CHAT_ID_INT and owner_debt > 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Получены деньги от владельца. Закрыть период?",
            reply_markup=kb_owner_paid(),
        )


    if chat_id != OWNER_CHAT_ID_INT:
        return

    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:AC",
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

    owner_debt = 0

    for r in rows[1:]:
        # AB = 27, AC = 28 (0-based)
        if len(r) > 28 and r[28] == "unpaid":
            try:
                owner_debt += int(r[27])
            except Exception:
                pass


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
        "\n\n💸 <b>Сервисный сбор</b>\n"
        f"• К оплате: <b>{_fmt_money(owner_debt)}</b>"
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
    log.info(
        f"[CHECKOUT REPLY] chat={update.effective_chat.id} "
        f"text={update.message.text!r} "
        f"step={context.user_data.get('checkout')}"
    )
    if update.effective_chat.id in STAFF_CHAT_IDS:
        return

    checkout = context.user_data.get("checkout")
    if not checkout:
        return

    step = checkout.get("step")


    msg = update.message
    if not msg:
        return

    chat_id = msg.chat_id
    text = (msg.text or "").strip()

    # --- ЭТАП 1: ИМЯ ---
    if step == "ask_name":
        if not text:
            await msg.reply_text("❌ Пожалуйста, введите имя.")
            return

        checkout["real_name"] = text
        checkout["step"] = "ask_phone"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📞 <b>Ваш номер телефона</b>\n\n"
                "Введите номер для связи ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        
        return

    # --- ЭТАП 2: ТЕЛЕФОН ---
    if step == "ask_phone":
        if not text:
            await msg.reply_text("❌ Пожалуйста, введите номер телефона.")
            return

        checkout["phone_number"] = text
        checkout["step"] = "type"

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

        checkout["address"] = text
        checkout["step"] = "comment"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✍️ Напишите комментарий к заказу.\n\n"
                "• Например: удобное время доставки\n\n"
                "⬇️ Ответьте на это сообщение"
            ),
            reply_markup=None,
        )
        
        return

    # --- ЭТАП 3: КОММЕНТАРИЙ ---
    if step != "comment":
        return

    if not text:
        await msg.reply_text("✍️ Напишите комментарий или '-'")
        return

    checkout["comment"] = text
    checkout["step"] = "preview"

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
        if not checkout or checkout.get("step") != "ready_to_send":
            log.warning("⛔ final_send ignored: wrong checkout state")
            return

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

        # cleanup
        context.user_data.pop("checkout", None)
        context.user_data["cart"] = {}

        await clear_ui(context, chat_id)
        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ <b>Заказ отправлен</b>\n\n"
                "Ваш заказ ушел в обработку.\n"
                "Скоро вы получите подтверждение"
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

        init_checkout(context)
        checkout = context.user_data["checkout"]
        checkout["step"] = "ask_name"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✍️ <b>Как вас зовут?</b>\n\n"
                "Введите ваше имя и фамилию ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        
        return

    if data.startswith("checkout:type:"):
        kind = data.split(":")[-1]

        checkout = context.user_data.setdefault("checkout", {})
        checkout["type"] = kind

        # 🚚 ДОСТАВКА → СПРАШИВАЕМ АДРЕС
        if kind == "delivery":
            checkout["step"] = "ask_address"

            m = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📍 <b>Укажите адрес доставки</b>\n\n"
                    "Введите адрес <b>на корейском языке</b>.\n"
                    "Это нужно для правильной навигации курьера ⬇️"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            
            return

        # 🚶 САМОВЫВОЗ → СРАЗУ К КОММЕНТАРИЮ
        checkout["step"] = "comment"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "💬 <b>Комментарий к заказу</b>\n\n"
                "Если есть пожелания, напишите их сообщением ниже.\n"
                "Если комментарий не нужен, просто отправьте «-» ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        
        return
    
    if data == "checkout:attach":
        checkout = context.user_data.get("checkout")
        if not checkout:
            return

        await clear_ui(context, chat_id)

        m = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📎 <b>На этом этапе необходимо произвести оплату на наш тонжан и прикрепить скриншот</b>\n\n"
                "Скриншот нужно отправить нажав на📎внизу экрана. <b> Без этого невозможно завершить заказ</b>.\n"
                "Нажмите 📎 внизу экрана ⬇️"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )

        checkout["step"] = "wait_photo"
        checkout["photo_reply_to"] = m.message_id
        
        return


    if data == "checkout:cancel":
        context.user_data.pop("checkout", None)
        context.user_data.pop("step", None)
        await render_cart(context, chat_id)
        return

async def on_buyer_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("📸 BUYER PAYMENT PHOTO HANDLER FIRED")
    msg = update.message
    if not msg or not msg.photo:
        return

    chat_id = msg.chat_id
    if chat_id in STAFF_CHAT_IDS:
        return

    checkout = context.user_data.get("checkout")
    if not checkout or checkout.get("step") != "wait_photo":
        return

    expected_reply_to = checkout.get("photo_reply_to")

    # reply_to у фото может отсутствовать, даже если ForceReply был
    if expected_reply_to:
        if msg.reply_to_message is not None:
            if msg.reply_to_message.message_id != expected_reply_to:
                return
    # если reply_to_message нет, но мы в wait_photo, принимаем фото все равно

    # берем самое большое фото
    file_id = msg.photo[-1].file_id
    checkout["payment_photo_file_id"] = file_id

    # уведомляем стаф, но не стопим поток если кто-то не может принять сообщение
    for staff_id in STAFF_CHAT_IDS:
        try:
            await context.bot.send_message(
                chat_id=staff_id,
                text="📸 Пришел скрин оплаты"
            )
        except Exception as e:
            log.error(f"❌ failed to notify staff {staff_id}: {e}")

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
        reply_markup=kb_checkout_send(),
    )
    track_msg(context, m.message_id)

    checkout["step"] = "ready_to_send"

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
        range="orders!A:O",  # ⬅️ до reaction_seconds
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
        # 1. статус
        new_status = "approved"

        # 2. комиссия (считаем сразу)
        try:
            total = int(target_row[5]) if len(target_row) > 5 and str(target_row[5]).isdigit() else 0
            platform_commission = int(total * 0.10)
        except Exception:
            platform_commission = 0

        commission_created_at = datetime.utcnow().isoformat()
        try:
            created_at = datetime.fromisoformat(target_row[1])
            handled_at = datetime.utcnow()
            reaction_seconds = int((handled_at - created_at).total_seconds())
        except Exception:
            handled_at = datetime.utcnow()
            reaction_seconds = ""
        # 3. апдейт заказа (ОДИН РАЗ)
        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!J{target_index}", "values": [["approved"]]},
                    {"range": f"orders!K{target_index}", "values": [[handled_at.isoformat()]]},
                    {"range": f"orders!L{target_index}", "values": [[str(chat_id)]]},
                    {"range": f"orders!M{target_index}", "values": [[reaction_seconds]]},

                    # комиссия
                    {"range": f"orders!AA{target_index}", "values": [[commission_created_at]]},
                    {"range": f"orders!AB{target_index}", "values": [[platform_commission]]},
                    {"range": f"orders!AC{target_index}", "values": [["unpaid"]]},
                ],
            },
        ).execute()

        # 4. следующий шаг — курьер
        await context.bot.send_message(
            chat_id=chat_id,
            text="Через сколько должен приехать курьер?",
            reply_markup=kb_staff_pickup_eta(order_id),
        )

        # 5. чистим старое сообщение
        try:
            await q.message.delete()
        except Exception:
            pass

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
                {"range": f"orders!J{target_index}", "values": [[new_status]]},
                {"range": f"orders!K{target_index}", "values": [[handled_at.isoformat()]]},
                {"range": f"orders!L{target_index}", "values": [[str(chat_id)]]},
                {"range": f"orders!M{target_index}", "values": [[reaction_seconds]]},

                # ✅ ВАЖНО
                {"range": f"orders!P{target_index}", "values": [["handled"]]},
                {"range": f"orders!Q{target_index}", "values": [[""]]},
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
        await q.message.delete()
    except Exception:
        pass

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
            reply_markup=None,
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

SHOP_NAME = "БАРАКАТ"
SHOP_PHONE = "010-8207-4445"
SHOP_NOTE = "Традиционная узбекская кухня. ХАЛАЛ"
FREE_DELIVERY_FROM = 30000
DELIVERY_FEE = 4000
# -------------------------
# webapi - handlers
# -------------------------

async def on_staff_eta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    _, _, minutes, order_id = q.data.split(":", 3)
    minutes = int(minutes)

    pickup_eta_at = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()

    service = get_sheets_service()
    sheet = service.spreadsheets()

    # --- 5.1 защита от повторного решения ---
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:T",
    ).execute().get("values", [])

    target_idx = None
    current_status = ""
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            current_status = r[19] if len(r) > 19 else ""  # колонка T
            break

    if not target_idx:
        return

    if current_status in ("courier_requested", "courier_not_requested"):
        await q.answer("Решение по курьеру уже принято", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    
    # --- конец защиты ---

    # найти строку заказа
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:T",
    ).execute().get("values", [])

    target_idx = None
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            break
    if not target_idx:
        return

    sheet.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"orders!R{target_idx}", "values": [[pickup_eta_at]]},
                {"range": f"orders!S{target_idx}", "values": [["preset"]]},
                {"range": f"orders!T{target_idx}", "values": [["courier_requested"]]},
            ],
        },
    ).execute()

    payload = build_courier_payload(rows[target_idx - 1])
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"orders!V{target_idx}",
        valueInputOption="RAW",
        body={"values": [[json.dumps(payload, ensure_ascii=False)]]},
    ).execute()


    # уведомляем клиента
    buyer_chat_id = int(rows[target_idx-1][2])
    await context.bot.send_message(
        chat_id=buyer_chat_id,
        text=(
            "Ваш заказ принят в работу.\n"
            "Вы можете отслеживать доставку в боте курьерской службы."
        ),
    )

    ok = await send_to_courier_and_persist(rows[target_idx - 1], target_idx)

    try:
        await q.message.delete()
    except Exception:
        pass

async def on_staff_no_courier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    _, _, order_id = q.data.split(":", 2)

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:T",
    ).execute().get("values", [])

    # защита от повторных действий
    service = get_sheets_service()
    sheet = service.spreadsheets()
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:T",
    ).execute().get("values", [])

    target_idx = None
    current_status = ""
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            current_status = r[19] if len(r) > 19 else ""  # колонка T
            break

    if not target_idx:
        return

    if current_status in ("courier_requested", "courier_not_requested"):
        await q.answer("Решение по курьеру уже принято", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    target_idx = None
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            break
    if not target_idx:
        return

    sheet.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"orders!T{target_idx}", "values": [["courier_not_requested"]]},
                {"range": f"orders!U{target_idx}", "values": [[""]]},  # courier_no_reason (резерв)
            ],
        },
    ).execute()

    buyer_chat_id = int(rows[target_idx-1][2])
    await context.bot.send_message(
        chat_id=buyer_chat_id,
        text="Ваш заказ принят. Курьер вызываться не будет.",
    )

    try:
        await q.message.delete()
    except Exception:
        pass

def set_waiting_manual_eta(context: ContextTypes.DEFAULT_TYPE, order_id: str):
    context.user_data["waiting_manual_eta"] = order_id

def pop_waiting_manual_eta(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.pop("waiting_manual_eta", None)

async def on_staff_eta_manual_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    _, _, order_id = q.data.split(":", 2)

    set_waiting_manual_eta(context, order_id)

    # защита от повторных действий
    service = get_sheets_service()
    sheet = service.spreadsheets()
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:T",
    ).execute().get("values", [])

    target_idx = None
    current_status = ""
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            current_status = r[19] if len(r) > 19 else ""  # колонка T
            break

    if not target_idx:
        return

    if current_status in ("courier_requested", "courier_not_requested"):
        await q.answer("Решение по курьеру уже принято", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🕒 <b>Введите дату и время прибытия курьера</b>\n\n"
            "Формат: <code>DD.MM HH:MM</code>\n"
            "Пример: <code>28.01 18:30</code>"
        ),
        parse_mode=ParseMode.HTML,
    )
    ok = await send_to_courier_and_persist(rows[target_idx - 1], target_idx)
    try:
        await q.message.delete()
    except Exception:
        pass


def build_courier_payload(order_row: list) -> dict:
    return {
        "order_id": order_row[0],
        "pickup_address": "KITCHEN_ADDRESS",  # позже из Sheets кухни
        "dropoff_address": order_row[13] if len(order_row) > 13 else "",
        "pickup_eta_at": order_row[17] if len(order_row) > 17 else "",
        "customer": {
            "name": "",   # можно подтянуть из users
            "phone": "",
        },
        "comment": order_row[7] if len(order_row) > 7 else "",
    }

import httpx
import time

COURIER_API_BASE = os.getenv("COURIER_API_BASE", "")
COURIER_API_KEY  = os.getenv("COURIER_API_KEY", "")
COURIER_TIMEOUT  = 10

async def courier_create_order(payload: dict) -> dict:
    """
    Реальный вызов курьерки.
    Должен быть идемпотентным по order_id.
    """
    if not COURIER_API_BASE:
        # dev-safe: имитируем успех
        return {
            "ok": True,
            "external_id": f"DEV-{payload.get('order_id')}",
        }

    headers = {
        "Authorization": f"Bearer {COURIER_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=COURIER_TIMEOUT) as client:
        r = await client.post(
            f"{COURIER_API_BASE}/orders",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "ok": True,
            "external_id": data.get("id"),
        }


async def send_to_courier_and_persist(order_row: list, target_idx: int):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    payload = json.loads(order_row[21]) if len(order_row) > 21 and order_row[21] else build_courier_payload(order_row)

    try:
        res = await courier_create_order(payload)
        if not res.get("ok"):
            raise RuntimeError("courier response not ok")

        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!W{target_idx}", "values": [[res.get("external_id", "")]]},
                    {"range": f"orders!T{target_idx}", "values": [["courier_created"]]},
                    {"range": f"orders!X{target_idx}", "values": [["ok"]]},
                    {"range": f"orders!Y{target_idx}", "values": [[""]]},
                    {"range": f"orders!Z{target_idx}", "values": [[datetime.utcnow().isoformat()]]},
                ],
            },
        ).execute()
        return True

    except Exception as e:
        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!X{target_idx}", "values": [["failed"]]},
                    {"range": f"orders!Y{target_idx}", "values": [[str(e)[:500]]]},
                ],
            },
        ).execute()
        return False


async def on_staff_courier_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    _, _, order_id = q.data.split(":", 2)

    service = get_sheets_service()
    sheet = service.spreadsheets()
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:Z",
    ).execute().get("values", [])

    target_idx = None
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            break
    if not target_idx:
        return

    await send_to_courier_and_persist(rows[target_idx - 1], target_idx)

import uuid

async def on_owner_commission_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.message.chat_id != ADMIN_CHAT_ID_INT:
        return

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:AC",
    ).execute().get("values", [])

    unpaid_rows = []
    total_amount = 0
    dates = []

    for r in rows[1:]:
        if len(r) > 28 and r[28] == "unpaid":
            try:
                total_amount += int(r[27])
            except Exception:
                pass

            if len(r) > 26 and r[26]:
                dates.append(r[26])  # delivery_confirmed_at

            unpaid_rows.append(r)

    if not unpaid_rows:
        await q.answer("Нет задолженности", show_alert=True)
        return

    payment_id = str(uuid.uuid4())
    paid_at = datetime.utcnow().isoformat()
    period_from = min(dates) if dates else ""
    period_to = max(dates) if dates else ""
    orders_count = len(unpaid_rows)

    # 1. пишем платеж
    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="payments!A:H",
        valueInputOption="RAW",
        body={
            "values": [[
                payment_id,
                paid_at,
                total_amount,
                period_from,
                period_to,
                orders_count,
                str(ADMIN_CHAT_ID_INT),
                "",
            ]]
        },
    ).execute()

    # 2. закрываем комиссии
    updates = []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > 28 and r[28] == "unpaid":
            updates.append(
                {"range": f"orders!AC{i}", "values": [["paid"]]}
            )

    if updates:
        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": updates,
            },
        ).execute()

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID_INT,
        text=(
            "✅ Платеж зафиксирован\n\n"
            f"💰 Сумма: {_fmt_money(total_amount)}\n"
            f"📦 Заказов: {orders_count}"
        ),
    )

    try:
        await q.message.delete()
    except Exception:
        pass


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

    order_id = context.user_data.get("waiting_manual_eta")
    if order_id:
        text = (update.message.text or "").strip()

        try:
            dt = datetime.strptime(text, "%d.%m %H:%M")
            now = datetime.utcnow()
            dt = dt.replace(year=now.year)

            if dt < now:
                await update.message.reply_text("❌ Время должно быть в будущем.")
                return

        except Exception:
            await update.message.reply_text(
                "❌ Неверный формат.\nИспользуйте: DD.MM HH:MM"
            )
            return

        service = get_sheets_service()
        sheet = service.spreadsheets()

        rows = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="orders!A:T",
        ).execute().get("values", [])

        target_idx = None
        for i, r in enumerate(rows[1:], start=2):
            if r and r[0] == order_id:
                target_idx = i
                break

        if not target_idx:
            context.user_data.pop("waiting_manual_eta", None)
            return

        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!R{target_idx}", "values": [[dt.isoformat()]]},
                    {"range": f"orders!S{target_idx}", "values": [["manual"]]},
                    {"range": f"orders!T{target_idx}", "values": [["courier_requested"]]},
                ],
            },
        ).execute()

        buyer_chat_id = int(rows[target_idx - 1][2])
        await context.bot.send_message(
            chat_id=buyer_chat_id,
            text=(
                "Ваш заказ принят в работу.\n"
                "Вы можете отслеживать доставку в боте курьерской службы."
            ),
        )

        context.user_data.pop("waiting_manual_eta", None)
        await update.message.reply_text("✅ Время курьера сохранено.")
        return


    chat_id = update.effective_chat.id
    if chat_id not in STAFF_CHAT_IDS:
        return

    if "broadcast" in context.user_data:
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

def init_checkout(context):
    context.user_data["checkout"] = {
        "step": None,
        "real_name": None,
        "phone_number": None,
        "type": None,          # pickup | delivery
        "address": None,
        "comment": None,
        "payment_photo_file_id": None,
    }


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

async def notify_staff(bot, order_id: str):
    log.error("🔥🔥🔥 notify_staff CALLED")
    service = get_sheets_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:Q",
    ).execute().get("values", [])

    if len(rows) < 2:
        return None

    order_row = None
    for r in rows[1:]:
        if r and r[0] == order_id:
            order_row = r
            break

    if not order_row:
        log.warning(f"order {order_id} not found")
        return None
    
    order_id        = order_row[0]
    created_at      = order_row[1]
    buyer_chat_id   = order_row[2]
    items           = order_row[4] if len(order_row) > 4 else ""
    total           = int(order_row[5]) if len(order_row) > 5 and str(order_row[5]).isdigit() else 0
    kind            = order_row[6] if len(order_row) > 6 else ""
    comment         = order_row[7] if len(order_row) > 7 else ""
    payment_file_id = order_row[8] if len(order_row) > 8 else ""
    status          = order_row[9] if len(order_row) > 9 else ""

    address         = order_row[13] if len(order_row) > 13 else ""
    delivery_fee    = int(order_row[14]) if len(order_row) > 14 and str(order_row[14]).isdigit() else 0
        
    if status != "pending":
        return None

    buyer_name = ""
    buyer_phone = ""

    service = get_sheets_service()
    users = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="users!A:F",
    ).execute().get("values", [])

    for u in users:
        if u and u[0] == buyer_chat_id:
            buyer_name = u[4] if len(u) > 4 else ""
            buyer_phone = u[5] if len(u) > 5 else ""
            break

    address_block = f"\n📍 <b>Адрес:</b>\n<code>{address}</code>\n" if address else ""

    delivery_line = ""
    if kind == "Доставка":
        delivery_line = (
            "🚚 <b>Доставка:</b> бесплатно\n"
            if delivery_fee == 0
            else f"🚚 <b>Доставка:</b> {_fmt_money(delivery_fee)}\n"
        )

    caption = (
        "🧨 TEST_NOTIFY_STAFF\n\n"
        "🛎 <b>Новый заказ</b>\n\n"
        f"🧾 ID: <code>{order_id}</code>\n\n"
        f"👤 <b>Имя:</b> {buyer_name or '—'}\n"
        f"📞 <b>Телефон:</b> <code>{buyer_phone or '—'}</code>\n"
        f"{address_block}"
        f"{items}\n\n"
        f"{delivery_line}"
        f"💰 Итого: <b>{_fmt_money(total)}</b>\n"
        f"🚚 Способ: <b>{kind}</b>\n"
        f"💬 Комментарий: <b>{comment or '—'}</b>"
    )

    first_msg = None

    for staff_id in STAFF_CHAT_IDS:
        try:
            if payment_file_id:
                msg = await bot.send_photo(
                    chat_id=staff_id,
                    photo=payment_file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=kb_staff_order(order_id),
                )
            else:
                msg = await bot.send_message(
                    chat_id=staff_id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=kb_staff_order(order_id),
                )

            if first_msg is None:
                first_msg = msg

        except Exception as e:
            log.warning(f"notify_staff failed for {staff_id}: {e}")

    return first_msg

def get_order_from_sheet(row: list) -> dict:
    def safe_int(val, default=0):
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    return {
        "customer": {
            "name": row[3] if len(row) > 3 else "",
            "phone": row[4] if len(row) > 4 else "",
            "deliveryType": row[5] if len(row) > 5 else "",
            "address": row[6] if len(row) > 6 else "",
            "comment": row[7] if len(row) > 7 else "",
        },
        "pricing": {
            "itemsTotal": safe_int(row[10] if len(row) > 10 else 0),
            "delivery": safe_int(row[11] if len(row) > 11 else 0),
            "grandTotal": safe_int(row[12] if len(row) > 12 else 0),
        },
        "items": [],  # позже можно подтянуть из отдельного листа
        "screenshotBase64": row[13] if len(row) > 13 and row[13] else None,
    }

from telegram import Bot

def build_checkout_preview(
    cart: dict,
    kind_label: str,
    comment: str,
    address: str | None = None,
) -> str:
    kind = "delivery" if kind_label == "Доставка" else "pickup"

    subtotal = cart_total(cart)
    delivery_fee = calc_delivery_fee(cart, kind)
    total = subtotal + delivery_fee

    address_block = (
        f"Адрес: <b>{address}</b>\n"
        if address else ""
    )

    delivery_block = ""
    if kind == "delivery":
        if delivery_fee == 0:
            delivery_block = "🚚 Доставка: <b>бесплатно</b>\n"
        else:
            delivery_block = f"🚚 Доставка: <b>{_fmt_money(delivery_fee)}</b>\n"

    return (
        "🧾 <b>Проверьте заказ</b>\n\n"
        f"{cart_text(cart)}\n\n"
        f"{delivery_block}"
        f"💰 <b>Итого к оплате: {_fmt_money(total)}</b>\n\n"
        f"Способ: <b>{kind_label}</b>\n"
        f"{address_block}"
        f"Комментарий: <b>{comment or '—'}</b>\n\n"
        "На этом этапе необходимо произвести оплату на наш тонжан и прикрепить скриншот ⬇️"
    )

def main():
    
    
    app = Application.builder().token(BOT_TOKEN).build()
    from webapp_orders_sync import webapp_orders_job

  #  app.job_queue.run_repeating(
  #      webapp_orders_job,
  #      interval=5,
  #      first=5,
  #      data={
  #          "spreadsheet_id": SPREADSHEET_ID,
  #      },
  #  )
    # -------- COMMANDS --------
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("help", help_cmd))  # ← ВОТ ЭТОГО НЕ ХВАТАЛО
    app.add_handler(CommandHandler("catalog", catalog_cmd))
    app.add_handler(CommandHandler("dash", dash_cmd))

    # -------- BUYER TEXT (checkout replies) --------
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Chat(STAFF_CHAT_IDS),
            on_checkout_reply
        ),
        group=1
    )
    # -------- WEB API --------

    app.add_handler(
        CallbackQueryHandler(on_staff_eta, pattern=r"^staff:eta:\d+:")
    )

    app.add_handler(
        CallbackQueryHandler(on_staff_no_courier, pattern=r"^staff:no_courier:")
    )

    app.add_handler(
        CallbackQueryHandler(
            on_staff_eta_manual_click,
            pattern=r"^staff:eta_manual:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(on_owner_commission_paid, pattern=r"^owner:commission_paid$")
    )


    app.add_handler(
        CallbackQueryHandler(on_staff_courier_retry, pattern=r"^staff:courier_retry:")
    )

    # -------- CALLBACKS (ВСЕ КНОПКИ) --------

    # ✅ ЕДИНСТВЕННЫЙ staff handler
    app.add_handler(
        CallbackQueryHandler(
            staff_callback,
            pattern=r"^staff:(approve|reject):"
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
            on_button,
            pattern=r"^(home:|nav:|cat:|prod:|cart:|checkout:)"
        )
    )

    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE)
            & ~filters.Chat(STAFF_CHAT_IDS),
            on_buyer_payment_photo
        )
    )
    # -------- STAFF --------
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.Chat(STAFF_CHAT_IDS),
            on_staff_photo
        )
    )
    
    # -------- STAFF TEXT --------
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Chat(STAFF_CHAT_IDS),
            on_staff_text
        ),
        group=10
    )

    # ✅ ВОТ СЮДА
    register_broadcast_handlers(
        app,
        owner_chat_id=OWNER_CHAT_ID_INT,
        staff_chat_ids=STAFF_CHAT_IDS,
        sheets_service=get_sheets_service(),
        spreadsheet_id=SPREADSHEET_ID,
    )

    
# -------- BUYER PHOTO (payment proof) --------
    
    log.info("Bot started")
    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "web_app_data",
        ],
        drop_pending_updates=True,
    )

def get_product_by_id(pid: str) -> dict | None:
    for p in read_products_from_sheets():
        if p["product_id"] == pid:
            return p
    return None

def get_categories_from_products(products: list[dict]) -> list[str]:
    return sorted({
        p["category"]
        for p in products
        if p["available"] and p.get("category")
    })

# -------------------------
# Web API stub (delivery / zones)
# -------------------------

def webapi_verify_address(address: str) -> dict:
    """
    Заглушка Web API.
    В будущем здесь будет HTTP-вызов.
    """
    return {
        "ok": True,
        "zone": "inside",   # inside | outside
        "distance_km": None,
        "cached": True,
    }


def webapi_calculate_delivery(cart: dict, address: str) -> dict:
    """
    Заглушка расчета доставки.
    Временно: фиксированная доставка 4000.
    """
    return {
        "ok": True,
        "price": 4000,
        "flag": "ok",  # ok | manual | too_far
    }

if __name__ == "__main__":
    main()