# main.py — MarketPlace EASYGO
# Требования:
# - Python + python-telegram-bot v20+

import os
import logging
logger = logging.getLogger("FlowerShopKR")
from typing import Dict, List, Optional
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from google.oauth2.service_account import Credentials


# -------------------------
# Web API client (safe import)
# -------------------------

try:
    from webapi_client import webapi_create_order
    WEBAPI_AVAILABLE = True
except ImportError:
    WEBAPI_AVAILABLE = False

    from kitchen_context import require

    async def webapi_create_order(payload: dict) -> dict:
        log.warning("⚠️ Web API unavailable, using STUB webapi_create_order")
        return {
            "status": "ok",
            "order_id": payload.get("order_id"),
            "address": {
                "verified": True,
                "mode": "stub",
            },
            "next": "courier_stubbed",
        }


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
    ApplicationBuilder,
)
from sheets_repo import get_sheets_service
from google.oauth2 import service_account
from googleapiclient.discovery import build
from broadcast import register_broadcast_handlers
from dotenv import load_dotenv
load_dotenv()
from courier_payload import build_courier_payload
from telegram.ext import CallbackQueryHandler
from staff_callbacks import staff_callback
from keyboards_staff import kb_staff_pickup_eta
from courier_api import courier_create_order
from marketplace_handlers import (
    marketplace_start,
    marketplace_select_kitchen,
    marketplace_callback,
    marketplace_back,
)
from types import SimpleNamespace
from marketplace_handlers import get_active_kitchen
from kitchen_context import KitchenContext
from config import BOT_TOKEN, ADMIN_IDS

HOME_PHOTO_FILE_ID = "AgACAgUAAxkBAAIBWml2tkzPZ3lgBPKTVeeA3Wi9Z3yJAAKuDWsbhLi4VyKeP_hEUISAAQADAgADeQADOAQ"
import inspect
import requests
from config import WEB_API_BASE_URL, WEB_API_KEY, WEB_API_TIMEOUT

import os
import json
import base64
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

def get_sheets_service():
    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if not b64:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 not set")

    info = json.loads(
        base64.b64decode(b64).decode("utf-8")
    )

    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    return build("sheets", "v4", credentials=creds)


logger = logging.getLogger(__name__)

def webapi_check_address(city: str, address: str) -> dict | None:
    try:
        logger.info(
            "[WEBAPI] address check city=%r address=%r url=%s",
            city,
            address,
            WEB_API_BASE_URL,
        )

        resp = requests.post(
            f"{WEB_API_BASE_URL}/api/v1/address/check",
            json={
                "city": city,
                "address": address,
            },
            headers={
                "X-API-KEY": WEB_API_KEY,
            },
            timeout=WEB_API_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.error(
                "[WEBAPI] address check failed %s %s",
                resp.status_code,
                resp.text,
            )
            return None

        return resp.json()

    except Exception as e:
        logger.exception("[WEBAPI] address check exception: %s", e)
        return None
    
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

_KITCHEN_ADDRESS_CACHE = {}

def get_kitchen_address_cached() -> str:
    if "address" in _KITCHEN_ADDRESS_CACHE:
        return _KITCHEN_ADDRESS_CACHE["address"]
    
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="kitchen!B1",
        ).execute()
        
        values = result.get("values", [])
        address = values[0][0] if values else "ADDRESS_NOT_SET"
        _KITCHEN_ADDRESS_CACHE["address"] = address
        return address
    except:
        return "ADDRESS_NOT_SET"

def get_kitchen_city_cached(*, kitchen: KitchenContext) -> str | None:
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=kitchen.spreadsheet_id,
            range="kitchen!C1",
        ).execute()

        values = result.get("values", [])
        if values and values[0]:
            return values[0][0].strip()
    except Exception:
        pass

    return None

def save_user_contacts(
    *,
    kitchen: KitchenContext,
    user_id: int,
    real_name: str,
    phone_number: str,
    telegram_chat_id: int | None = None,
) -> bool:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # читаем пользователей (с запасом под chat_id)
    result = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
        range="users!A2:G",
    ).execute()

    rows = result.get("values", [])
    target_row = None

    for idx, row in enumerate(rows, start=2):
        if row and row[0] == str(user_id):
            target_row = idx
            break

    if not target_row:
        return False

    updates = [
        {"range": f"users!E{target_row}", "values": [[real_name]]},
        {"range": f"users!F{target_row}", "values": [[phone_number]]},
    ]

    # 👇 НОВОЕ, но НЕ ломащее старый флоу
    if telegram_chat_id is not None:
        updates.append(
            {"range": f"users!G{target_row}", "values": [[str(telegram_chat_id)]]}
        )

    sheet.values().batchUpdate(
        spreadsheetId=kitchen.spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": updates,
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

def set_product_price(
    kitchen: "KitchenContext",
    product_id: str,
    price: int,
) -> bool:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
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

    customer_price = calc_customer_price(price)

    sheet.values().batchUpdate(
        spreadsheetId=kitchen.spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"products!C{row_index}", "values": [[price]]},
                {"range": f"products!M{row_index}", "values": [[customer_price]]},
            ],
        },
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

def calc_customer_price(owner_price: int) -> int:
    """
    customer_price = owner_price + 10%
    округление вверх до 100 вон
    """
    raw = int(owner_price * 1.1)
    return ((raw + 99) // 100) * 100

def safe_open_photo(path: str):
    try:
        return open(path, "rb")
    except Exception:
        return None

from typing import Optional

def read_products_from_sheets(
    kitchen: "KitchenContext",
) -> list[dict]:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    logger.info(
        f"[READ_PRODUCTS] kitchen_id={kitchen.kitchen_id} "
        f"spreadsheet_id={kitchen.spreadsheet_id} "
        f"range=products!A2:M"
    )

    try:
        meta = service.spreadsheets().get(
            spreadsheetId=kitchen.spreadsheet_id
        ).execute()

        sheet_titles = [
            s["properties"]["title"]
            for s in meta.get("sheets", [])
        ]

        logger.info(
            f"[READ_PRODUCTS] kitchen_id={kitchen.kitchen_id} "
            f"available_sheets={sheet_titles}"
        )
    except Exception as e:
        logger.error(
            f"[READ_PRODUCTS] metadata fetch failed "
            f"kitchen_id={kitchen.kitchen_id} err={e}"
        )

    try:
        result = sheet.values().get(
            spreadsheetId=kitchen.spreadsheet_id,
            range="products!A2:M",
        ).execute()
    except Exception:
        logger.exception(
            f"[READ_PRODUCTS] FAILED "
            f"kitchen_id={kitchen.kitchen_id} "
            f"spreadsheet_id={kitchen.spreadsheet_id}"
        )
        return []

    rows = result.get("values", [])
    products: list[dict] = []

    for row in rows:
        # минимально нужные поля: id, name, owner_price, available, category
        if len(row) < 5:
            continue

        try:
            owner_price = int(row[2])
        except Exception:
            continue  # битая строка, пропускаем

        if len(row) > 12 and row[12]:
            try:
                customer_price = int(row[12])
            except Exception:
                customer_price = calc_customer_price(owner_price)
        else:
            customer_price = calc_customer_price(owner_price)

        products.append({
            "product_id": row[0],
            "name": row[1],
            "owner_price": owner_price,
            "customer_price": customer_price,
            "available": str(row[3]).strip().lower() == "true",
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

def append_product_to_sheets(
    kitchen: "KitchenContext",
    name: str,
    price: int,
    category: str,
    description: str,
) -> str | None:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    product_id = f"P{uuid4().hex[:10]}"

    customer_price = calc_customer_price(price)

    row = [
        product_id,          # A
        name,                # B
        price,               # C owner_price
        "TRUE",              # D
        category,            # E
        "",                  # F photo
        description or "",   # G
        "", "", "", "", "",  # H–L
        customer_price,      # M customer_price
    ]

    try:
        sheet.values().append(
            spreadsheetId=kitchen.spreadsheet_id,
            range="products!A:G",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()
        return product_id
    except Exception:
        return None

def save_order_to_sheets(
    *,
    kitchen: KitchenContext,
    user,
    cart: dict,
    kind: str,
    comment: str,
    address: str | None = None,
    order_id: str | None = None,
    external_delivery_ref: str | None = None,
    delivery_fee: int | None = None,
    payment_photo_file_id: str | None = None,  # 👈 фото оплаты
) -> str | None:
    
    service = get_sheets_service()
    sheet = service.spreadsheets()

    items = []
    subtotal = 0

    for pid, qty in cart.items():
        p = get_product_by_id(pid, kitchen=kitchen)
        if not p:
            continue
        items.append(f"{p['name']} x{qty}")
        subtotal += p["customer_price"] * qty

    # доставка
    # если delivery_fee пришел извне (из checkout), используем его как источник истины
    if delivery_fee is None:
        delivery_fee = 0
        if kind == "Доставка":
            if subtotal < FREE_DELIVERY_FROM:
                delivery_fee = DELIVERY_FEE

    log.info(
        "[save_order_to_sheets] order_id=%s kind=%s subtotal=%s delivery_fee=%s total=%s",
        order_id,
        kind,
        subtotal,
        delivery_fee,
        subtotal + delivery_fee,
    )

    total = subtotal + delivery_fee

    order_id = order_id or str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    row_values = [
        order_id,                     # A order_id
        created_at,                   # B created_at
        str(user.id),                 # C user_id
        user.username or "",          # D username
        "; ".join(items),             # E items
        total,                        # F total_price
        kind,                         # G type
        comment or "",                # H comment
        payment_photo_file_id or "",  # I payment_proof
        "created",                    # J status
        "",                           # K handled_at
        "",                           # L handled_by
        "",                           # M reaction_seconds
        address or "",                # N address
        delivery_fee,                 # O delivery_fee
        "kitchen",                    # P source
        "",                           # Q staff_message_id
        "",                           # R pickup_eta_at
        "",                           # S eta_source
        "delivery_new" if kind == "Доставка" else "pickup",  # T delivery_state
        "",                           # U courier_status_raw
        external_delivery_ref or "",  # V courier_external_id
        "",                           # W courier_external_id (legacy)
        "",                           # X courier_status_detail
        "",                           # Y courier_last_error
        "",                           # Z courier_sent_at
        "",                           # AA delivery_confirmed_at
        "",                           # AB platform_commission
        "created",                    # AC commission_status
        "",                           # AD owner_debt_snapshot
        "",                           # AE seen_by_system
        "",                           # AF staff_notified
    ]

    log.info(
        "[save_order_to_sheets] order_id=%s kind=%s subtotal=%s delivery_fee=%s payment_proof=%s",
        order_id,
        kind,
        subtotal,
        delivery_fee,
        bool(payment_photo_file_id),
    )

    try:
        # Валидация ПЕРЕД записью
        #validate_order_row(row_values)
        
        # Находим следующую пустую строку
        existing = sheet.values().get(
            spreadsheetId=kitchen.spreadsheet_id,
            range=ORDERS_RANGE,
        ).execute().get("values", [])
        
        next_row = len(existing) + 1
        target_range = f"orders!A{next_row}:AD{next_row}"
        
        log.info(
            f"[save_order_to_sheets] Existing rows: {len(existing)}, "
            f"Writing to: {target_range}"
        )
        
        # Используем update вместо append для гарантии правильной позиции
        resp = sheet.values().update(
            spreadsheetId=kitchen.spreadsheet_id,
            range=target_range,
            valueInputOption="RAW",
            body={"values": [row_values]},
        ).execute()

        log.info(
            f"✅ ORDER WRITTEN: order_id={order_id} "
            f"range={resp.get('updatedRange')}"
        )
        return order_id

    except ValueError as e:
        log.exception(f"❌ ORDER VALIDATION FAILED: {e}")
        return None
    
    except Exception:
        log.exception(f"❌ ORDER WRITE FAILED: buyer={user.id}")
        return None
    

def kb_staff_order(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"staff:approve:{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"staff:reject:{order_id}"),
        ]
    ])

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def kb_staff_only_check(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🧾 Открыть чек",
                callback_data=f"payproof:{order_id}",
            )
        ]
    ])


def set_waiting_photo(context: ContextTypes.DEFAULT_TYPE, product_id: str):
    context.user_data["waiting_photo_for"] = product_id

def pop_waiting_photo(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.pop("waiting_photo_for", None)

def cart_total(cart: Dict[str, int], kitchen) -> int:
    total = 0
    for pid, qty in cart.items():
        p = get_product_by_id(pid, kitchen)
        if not p:
            continue
        total += p["customer_price"] * qty
    return total

def calc_delivery_fee(cart: dict, kind: str) -> int:
    if kind != "delivery":
        return 0

    # временно используем Web API stub
    result = webapi_calculate_delivery(cart, address=None)
    return int(result.get("price", 0))

def cart_text(cart: Dict[str, int], kitchen) -> str:
    if not cart:
        return "Корзина пустая."

    lines: List[str] = []
    for pid, qty in cart.items():
        p = get_product_by_id(pid, kitchen)
        if not p:
            continue
        lines.append(
            f"• {p['name']} × {qty} = {_fmt_money(p['customer_price'] * qty)}"
        )

    lines.append("")
    lines.append(f"Итого: {_fmt_money(cart_total(cart, kitchen))}")
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
        [InlineKeyboardButton("🔁 Сменить заведение", callback_data="market:back")],
    ])


def kb_checkout_send() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Отправить заказ", callback_data="checkout:final_send")],
        [InlineKeyboardButton("❌ Отмена", callback_data="checkout:cancel")],
    ])


def kb_products(category: str, kitchen) -> InlineKeyboardMarkup:
    products = read_products_from_sheets(kitchen)

    rows = []
    for p in products:
        if not p["available"]:
            continue
        if p["category"] != category:
            continue

        rows.append([
            InlineKeyboardButton(
                f"{p['name']} — {_fmt_money(p['customer_price'])}",
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
        [InlineKeyboardButton("✅ Оплачено", callback_data="owner:commission_paid_confirm")]
    ])

def kb_owner_paid_confirm():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, закрыть период", callback_data="owner:commission_paid_apply"),
            InlineKeyboardButton("❌ Отмена", callback_data="owner:commission_paid_cancel"),
        ]
    ])

def kb_confirm_profile():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Продолжить", callback_data="checkout:profile_ok")],
        [InlineKeyboardButton("✏️ Изменить данные", callback_data="checkout:profile_edit")],
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
def home_text(kitchen):
    return (
        f"<b>{kitchen.kitchen_id}</b>\n"
        f"Город: {kitchen.city}\n\n"
        "Выберите действие:"
    )



async def render_home(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    from marketplace_handlers import get_active_kitchen
    kitchen = get_active_kitchen(context)

    nav = _get_nav(context)
    nav["screen"] = "home"

    await clear_ui(context, chat_id)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=home_text(kitchen),  # 👈 передаем кухню
        parse_mode=ParseMode.HTML,
        reply_markup=kb_home(),
    )
    track_msg(context, msg.message_id)

from kitchen_context import KitchenContext

async def render_categories(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
):
    kitchen = get_active_kitchen(context)
    if not kitchen:
        await clear_ui(context, chat_id)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Каталог недоступен (заведение не выбрано). Нажмите /start",
        )
        track_msg(context, msg.message_id)
        return

    try:
        products = read_products_from_sheets(kitchen)
    except Exception:
        log.exception("[render_categories] read_products_from_sheets failed kitchen=%s", getattr(kitchen, "kitchen_id", None))
        await clear_ui(context, chat_id)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Каталог недоступен (заведение не выбрано). Нажмите /start.",
        )
        track_msg(context, msg.message_id)
        return

    categories = get_categories_from_products(products)

    await clear_ui(context, chat_id)

    if not categories:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="Каталог недоступен (заведение не выбрано). Нажмите /start.",
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
                caption=f"💐 <b>{p['name']}</b>\n{_fmt_money(p['customer_price'])}",
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
    kitchen = get_active_kitchen(context)
    p = get_product_by_id(pid, kitchen)
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
        f"Цена: <b>{_fmt_money(p['customer_price'])}</b>\n"
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

    kitchen = get_active_kitchen(context)

    await clear_ui(context, chat_id)

    text = "🧺 <b>Корзина</b>\n\n" + cart_text(cart, kitchen)
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

    kitchen = get_active_kitchen(context)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"📦 <b>{category}</b>\nВыберите позицию:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_products(category, kitchen),
    )
    track_msg(context, msg.message_id)

# -------------------------
# /start
# -------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    # базовая регистрация (как было)
    register_user_if_new(user)

    # фиксируем связку в памяти диалога
    context.user_data["user_id"] = user.id
    context.user_data["telegram_chat_id"] = chat_id

    kitchen_id = context.user_data.get("kitchen_id")

    # если кухня еще не выбрана, показываем маркетплейс
    if not kitchen_id:
        await marketplace_start(update, context)
        return

    # если кухня выбрана, пробуем тихо сохранить chat_id в users sheet (мультикухонность)
    try:
        from kitchen_context import require
        from sheets_users import save_user_contacts  # если функция лежит в другом файле, скажи и мы поправим импорт

        kitchen = require(kitchen_id)

        # тут не трогаем имя и телефон, если их нет
        real_name = (user.full_name or "").strip()
        phone_number = ""

        save_user_contacts(
            kitchen=kitchen,
            user_id=user.id,
            real_name=real_name,
            phone_number=phone_number,
            telegram_chat_id=chat_id,
        )
    except Exception:
        # важно: start не должен падать из-за синка контактов
        pass

    # обычный домашний экран
    await render_home(context, chat_id)

def get_client_chat_id(*, kitchen: KitchenContext, user_id: int) -> int | None:
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=kitchen.spreadsheet_id,
        range="users!A2:G",
    ).execute()

    for row in result.get("values", []):
        if row and row[0] == str(user_id):
            return int(row[6]) if len(row) > 6 and row[6] else None

    return None

async def dash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ORDERS_RANGE,
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📊 Дашборд\n\nЗаказов пока нет.",
        )
        return

    # --- считаем owner_debt СНАЧАЛА ---
    owner_debt = 0
    for r in rows[1:]:
        # AB = 27, AC = 28 (0-based)
        if len(r) > 28 and r[28] == "unpaid":
            try:
                owner_debt += int(r[27])
            except Exception:
                pass

    # --- админская кнопка ---
    if chat_id == ADMIN_IDS and owner_debt > 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Получены деньги от владельца. Закрыть период?",
            reply_markup=kb_owner_paid(),
        )

    # --- дальше дашборд владельца ---
    if chat_id != OWNER_CHAT_ID_INT:
        return

    now = datetime.utcnow()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    revenue_today = revenue_week = revenue_month = 0
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
    kitchen = get_active_kitchen(context)  # 👈 ВАЖНО: фикс скоупа

    log.info(
        f"[CHECKOUT REPLY] chat={update.effective_chat.id} "
        f"text={update.message.text!r} "
        f"step={context.user_data.get('checkout')} "
        f"kitchen={kitchen.kitchen_id if kitchen else None}"
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

        # 🔗 WEB API: verify address
        city_code = get_kitchen_city_cached(kitchen=kitchen) or "unknown"
        check = webapi_check_address(city_code, text)
        if not check or not check.get("ok"):
            await msg.reply_text(
                "❌ Адрес не прошел проверку.\n"
                "Проверьте написание и попробуйте снова."
            )
            return

        checkout["address"] = check.get("normalized_address", text)

        # КРИТИЧНО: получаем delivery_price из Web API
        # Если цена не пришла — флоу ДОЛЖЕН остановиться
        delivery_price = check.get("delivery_price")

        if delivery_price is None:
            log.error(
                "[CHECKOUT] Web API did NOT return delivery_price! "
                f"address={text!r} check={check!r}"
            )
            await msg.reply_text(
                "❌ Ошибка расчета доставки\n\n"
                "Не удалось определить стоимость. "
                "Попробуйте изменить адрес или свяжитесь с поддержкой."
            )
            return

        checkout["delivery_price_krw"] = int(delivery_price)
        checkout["distance_km"] = check.get("distance_km")

        distance_km = check.get("distance_km", 0)

        log.info(
            "[DELIVERY_PRICE_RESOLVED] "
            f"address={text!r} "
            f"price={checkout['delivery_price_krw']} "
            f"distance_km={distance_km}"
        )

        # Если вне зоны — спрашиваем подтверждение
        if distance_km and distance_km > 4.0:
            checkout["step"] = "confirm_price"
            
            await msg.reply_text(
                f"📍 Адрес подтверждён\n\n"
                f"⚠️ Адрес вне стандартной зоны доставки\n"
                f"📏 Расстояние: {distance_km} км\n"
                f"🚚 Стоимость доставки: {checkout['delivery_price_krw']:,}₩\n\n"  # ← ИСПРАВЛЕНО
                f"Продолжить оформление?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Согласен", callback_data="checkout:price_ok"),
                        InlineKeyboardButton("❌ Отмена", callback_data="checkout:price_cancel"),
                    ]
                ])
            )
            return
        
        # В зоне — сразу к комментарию
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

    kitchen = get_active_kitchen(context)

    preview_text = build_checkout_preview(
        cart=cart,
        kitchen=kitchen,
        kind_label=kind_label,
        comment=text,
        address=checkout.get("address"),
        delivery_price_krw=checkout.get("delivery_price_krw"),
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
    user = q.from_user
    chat_id = q.message.chat_id
    
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

    if data == "checkout:price_ok":
        checkout = context.user_data.get("checkout", {})
        checkout["step"] = "comment"
        
        await q.message.edit_text(
            "✅ Цена подтверждена\n\n"
            "✍️ Напишите комментарий к заказу.\n"
            "• Например: удобное время доставки"
        )
        return

    if data == "checkout:price_cancel":
        context.user_data.pop("checkout", None)
        await q.message.edit_text("❌ Заказ отменён")
        return

    # ---------- CHECKOUT ----------

    if data == "checkout:final_send":
        checkout = context.user_data.get("checkout")
        if not checkout or checkout.get("step") != "ready_to_send":
            log.warning("⛔ final_send ignored: wrong checkout state")
            return
        
            # защита: доставка без verified адреса невозможна
        if checkout.get("type") == "delivery":
            if not checkout.get("address") or checkout.get("delivery_price_krw") is None:
                log.warning(
                    "⛔ final_send blocked: delivery data incomplete | checkout=%s",
                    checkout,
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Адрес доставки не подтвержден. Повторите ввод адреса.",
                )
                return
            
        payment_file_id = checkout.get("payment_photo_file_id")
        if not payment_file_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Сначала прикрепите скриншот оплаты.",
            )
            return

        cart = _get_cart(context)
        if not cart:
            log.warning("⛔ final_send ignored: empty cart")
            return

        kind = checkout.get("type", "pickup")
        kind_label = "Самовывоз" if kind == "pickup" else "Доставка"
        comment = checkout.get("comment", "")

        user = q.from_user
        
        
        import uuid
        
        order_id = str(uuid.uuid4())
        kitchen = get_active_kitchen(context)
        pickup_address = get_kitchen_address_cached(kitchen=kitchen)
        city_code = get_kitchen_city_cached(kitchen=kitchen) or "unknown"

        if not pickup_address:
            pickup_address = "KITCHEN_ADDRESS_NOT_SET"

        if not city_code:
            city_code = "CITY_NOT_SET"

        # 🔒 Гарантия pickup_eta_at для доставки
        if checkout.get("type") == "delivery":
            if not checkout.get("pickup_eta_at"):
                from datetime import datetime, UTC
                checkout["pickup_eta_at"] = datetime.now(UTC).isoformat()

        order_payload = {
            "order_id": order_id,
            "source": "kitchen",
            "kitchen_id": kitchen.kitchen_id,
            "client_tg_id": user.id,
            "client_name": checkout.get("real_name"),
            "client_phone": checkout.get("phone_number"),
            "pickup_address": pickup_address,
            "delivery_address": checkout.get("address", ""),
            "pickup_eta_at": checkout.get("pickup_eta_at"),  # если есть
            "city": city_code,
            "comment": comment,
            "price_krw": checkout.get("delivery_price_krw", 0),  # 👈 ДОБАВИТЬ
        }

        # --- Web API create order ---
        try:
            from webapi_client import webapi_create_order
        except ImportError:
            log.warning("⚠️ webapi_create_order not available, using stub")

            async def webapi_create_order(payload):
                return {
                    "status": "ok",
                    "external_delivery_ref": None,  # НОРМА для самовывоза
                }
       
        # 🔍 DEBUG: payload перед отправкой в Web API
        log.info(
            "[WEBAPI_CREATE_ORDER_CALL] order_id=%s type=%s pickup_eta_at=%s courier_requested=%s payload=%s",
            order_payload.get("order_id"),
            checkout.get("type"),
            order_payload.get("pickup_eta_at"),
            bool(order_payload.get("pickup_eta_at")),
            order_payload,
        )

        # 🔒 гарантия pickup_eta_at для доставки (чтобы Web API создал доставку)
        if checkout.get("type") == "delivery" and not order_payload.get("pickup_eta_at"):
            order_payload["pickup_eta_at"] = datetime.utcnow().isoformat()

        # 🔍 DEBUG: payload перед отправкой в Web API
        log.info(
            "[WEBAPI_CREATE_ORDER_CALL] order_id=%s type=%s pickup_eta_at=%s url=%s",
            order_payload.get("order_id"),
            checkout.get("type"),
            order_payload.get("pickup_eta_at"),
            os.getenv("WEB_API_URL", ""),
        )

        try:
            resp = await webapi_create_order(order_payload)
        except Exception:
            log.exception("❌ Web API order create failed")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Не удалось создать заказ. Попробуйте позже.",
            )
            return

        if not resp or resp.get("status") != "ok":
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Заказ не принят системой. Попробуйте позже.",
            )
            return

        # ⬇️ ВАЖНО: stub / самовывоз / dev режим
        external_delivery_ref = resp.get("external_delivery_ref")

        is_stub = resp.get("external_delivery_ref") is None

        if checkout.get("type") == "delivery":
            if not is_stub and not external_delivery_ref:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Не удалось создать доставку. Попробуйте позже.",
                )
                return

        # самовывоз — НИКАКОЙ доставки
        if kind == "pickup":
            external_delivery_ref = None
        elif is_stub:
            external_delivery_ref = "STUB"
        else:
            external_delivery_ref = resp.get("external_delivery_ref")

        # ⬇️ ТОЛЬКО ТЕПЕРЬ пишем в Sheets
        saved = save_order_to_sheets(
            kitchen=kitchen,                # 👈 ВАЖНО
            user=user,
            cart=cart,
            kind=kind_label,
            comment=comment,
            address=checkout.get("address"),
            order_id=order_id,
            external_delivery_ref=external_delivery_ref,
            delivery_fee=checkout.get("delivery_price_krw"),
            payment_photo_file_id=checkout.get("payment_photo_file_id"),
        )
        #await notify_staff(context.bot, kitchen, order_id)
        #save_user_contacts(
        #    kitchen=kitchen,                # 👈 обязательно
        #    user_id=user.id,
        #    real_name=checkout.get("real_name"),
        #    phone_number=checkout.get("phone_number"),
        #)

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

        kitchen = get_active_kitchen(context)
        profile = get_user_profile(kitchen, q.from_user.id)
        
        if profile and profile.get("real_name") and profile.get("phone_number"):
            checkout.update({
                "real_name": profile["real_name"],
                "phone_number": profile["phone_number"],
                "step": "confirm_profile",
            })
            show_confirm_profile()
            return
        if profile and profile.get("name") and profile.get("phone"):
            checkout["real_name"] = profile["name"]
            checkout["phone_number"] = profile["phone"]
            checkout["step"] = "confirm_profile"

            await clear_ui(context, chat_id)
            m = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📋 <b>Ваши данные</b>\n\n"
                    f"👤 Имя: <b>{profile['name']}</b>\n"
                    f"📞 Телефон: <b>{profile['phone']}</b>\n\n"
                    "⚠️ Если данные изменились, нажмите «Изменить данные»."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_confirm_profile(),
            )
            track_msg(context, m.message_id)
            return
        else:
            checkout["step"] = "ask_name"

            m = await context.bot.send_message(
                chat_id=chat_id,
                text="✍️ <b>Как вас зовут?</b>\n\nВведите ваше имя и фамилию ⬇️",
                parse_mode=ParseMode.HTML,
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
    
    if data == "checkout:profile_ok":
        checkout = context.user_data.get("checkout")
        if not checkout or checkout.get("step") != "confirm_profile":
            return

        checkout["step"] = "type"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text="🚚 <b>Выберите способ получения:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_checkout_pickup_delivery(),
        )
        track_msg(context, m.message_id)
        return
    
    if data == "checkout:profile_edit":
        checkout = context.user_data.get("checkout")
        if not checkout:
            return

        checkout["step"] = "ask_name"

        m = await context.bot.send_message(
            chat_id=chat_id,
            text="✍️ <b>Как вас зовут?</b>\n\nВведите ваше имя и фамилию ⬇️",
            parse_mode=ParseMode.HTML,
        )
        track_msg(context, m.message_id)
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



    # показываем подтверждение + кнопку отправки
    cart = _get_cart(context)
    kind = checkout.get("type", "pickup")
    kind_label = "Самовывоз" if kind == "pickup" else "Доставка"
    comment = checkout.get("comment", "")

    kitchen = get_active_kitchen(context)

    preview_text = build_checkout_preview(
        cart=cart,
        kitchen=kitchen,
        kind_label=kind_label,
        comment=checkout.get("comment"),
        address=checkout.get("address"),
        delivery_price_krw=checkout.get("delivery_price_krw"),
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
    context.user_data["checkout"] = checkout
    log.error(
        f"PHOTO SAVED: {context.user_data.get('checkout')}"
    )

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
        range=ORDERS_RANGE,
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

    buyer_user_id = int(order_row[2])

    buyer_chat_id = get_client_chat_id(
        kitchen=kitchen,
        user_id=buyer_user_id,
    )

    if buyer_chat_id:
        await context.bot.send_message(
            chat_id=buyer_chat_id,
            text="Ваш заказ принят",
        )
    else:
        log.info(
            f"Client notification skipped: no telegram_chat_id "
            f"(user_id={buyer_user_id})"
        )

    # --- действие ---
    if action == "approve":
        new_status = "approved"

        # комиссия
        try:
            platform_commission = 0
            cart = parse_items_from_order(target_row[4])

            for pid, qty in cart.items():
                p = get_product_by_id(pid)
                if not p:
                    continue
                platform_commission += (p["customer_price"] - p["owner_price"]) * qty
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

        # --- основной апдейт заказа ---
        sheet.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!J{target_index}", "values": [[new_status]]},
                    {"range": f"orders!K{target_index}", "values": [[handled_at.isoformat()]]},
                    {"range": f"orders!L{target_index}", "values": [[str(chat_id)]]},
                    {"range": f"orders!M{target_index}", "values": [[reaction_seconds]]},

                    {"range": f"orders!AA{target_index}", "values": [[commission_created_at]]},
                    {"range": f"orders!AB{target_index}", "values": [[platform_commission]]},
                    {"range": f"orders!AC{target_index}", "values": [["unpaid"]]},

                    # курьер тут НЕ вызываем, только переводим в ожидание ETA (staff_eta)
                    {"range": f"orders!T{target_index}", "values": [["courier_pending_eta"]]},
                ],
            },
        ).execute()

        log.info(
            f"➡️ order {order_id}: approved, moved to courier_pending_eta (waiting staff ETA)"
        )

        # --- следующий шаг: выбор ETA ---
        # 🛑 защита от повторного показа ETA
        if current_status == "courier_pending_eta":
            try:
                await q.message.delete()
            except Exception:
                pass
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text="Через сколько должен приехать курьер?",
            reply_markup=kb_staff_pickup_eta(order_id, kitchen_id=kitchen.kitchen_id),
        )

        try:
            await q.message.delete()
        except Exception:
            pass

        return

    # дальше твой код без изменений (reject ветка и тд)

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
    buyer_user_id = int(target_row[2])

    from sheets_users import get_client_chat_id

    buyer_chat_id = get_client_chat_id(
        kitchen=kitchen,
        user_id=buyer_user_id,
    )

    # --- сообщение покупателю ---
    if buyer_chat_id:
        await context.bot.send_message(
            chat_id=buyer_chat_id,
            text=buyer_text,
        )
    else:
        log.info(
            f"Client notification skipped: no telegram_chat_id "
            f"(user_id={buyer_user_id}, order={order_id})"
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

def parse_payment_proof(value: str) -> str | None:
    """
    value ожидается вида:
    upload_xxx
    или
    https://...
    """
    if not value:
        return None

    value = str(value).strip()

    if value.startswith("http://") or value.startswith("https://"):
        return value

    if value.startswith("upload_"):
        try:
            import os
            web_api_url = os.getenv("WEB_API_URL")
            if not web_api_url:
                return None

            return f"{web_api_url}/api/v1/uploads/{value}"
        except Exception:
            return None

    return None

async def on_staff_eta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("=== ENTER on_staff_eta ===")

    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    # 1️⃣ Парсинг callback: staff:eta:10:kitchen_2:ORDER_123
    parts = q.data.split(":")
    if len(parts) != 5:
        log.error(f"Invalid ETA callback format: {q.data}")
        return
    
    _, _, minutes_str, kitchen_id, order_id = parts
    
    try:
        minutes = int(minutes_str)
    except ValueError:
        log.error(f"Invalid minutes: {minutes_str}")
        return
    
    log.info(f"ETA: kitchen_id={kitchen_id}, order_id={order_id}, minutes={minutes}")

    # 2️⃣ Получение kitchen ИЗ CALLBACK (НЕ из context.user_data!)
    from kitchen_context import require
    
    try:
        kitchen = require(kitchen_id)
    except Exception as e:
        log.error(f"Kitchen {kitchen_id} not found: {e}")
        await q.answer("Кухня недоступна", show_alert=True)
        return
    
    spreadsheet_id = kitchen.spreadsheet_id
    log.info(f"Kitchen resolved: spreadsheet_id={spreadsheet_id}")

    # 3️⃣ Подключение к Sheets
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # 4️⃣ Формирование ETA
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    eta_dt = now + timedelta(minutes=minutes)
    pickup_eta_at = eta_dt.isoformat()

    # 5️⃣ Поиск заказа в ПРАВИЛЬНОЙ таблице
    rows = sheet.values().get(
        spreadsheetId=spreadsheet_id,  # ✅ ИЗ KITCHEN
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    target_idx = None
    current_status = ""

    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == order_id:
            target_idx = i
            current_status = r[19] if len(r) > 19 else ""
            break

    if not target_idx:
        log.error(f"Order {order_id} not found in kitchen {kitchen_id}")
        return

    # ✅ ВАЖНО: гарантированно получаем строку заказа ДО работы с comment
    if target_idx - 1 >= len(rows):
        log.error(f"Order row index out of range: idx={target_idx}")
        return

    order_row = rows[target_idx - 1]

    # 6️⃣ Защита от повторного решения
    if current_status in ("courier_requested", "courier_not_requested"):
        await q.answer("Решение по курьеру уже принято", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # 7️⃣ Обновление ETA + comment в ПРАВИЛЬНОЙ таблице
    order_row_before = rows[target_idx - 1]

    try:
        # ✅ НОВОЕ: Формируем обновлённый комментарий
        # comment = колонка AA (индекс 26)
        COMMENT_COL_IDX = 26

        existing_comment = (
            order_row[COMMENT_COL_IDX]
            if len(order_row) > COMMENT_COL_IDX
            else ""
        )

        eta_note = f"Курьер через {minutes} мин"

        # Если комментарий уже есть, добавляем через разделитель
        if existing_comment and existing_comment.strip():
            new_comment = f"{existing_comment} | {eta_note}"
        else:
            new_comment = eta_note

        log.info(
            f"[ETA_COMMENT] order={order_id} "
            f"old='{existing_comment}' new='{new_comment}'"
        )

        sheet.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!R{target_idx}", "values": [[pickup_eta_at]]},
                    {"range": f"orders!S{target_idx}", "values": [["preset"]]},
                    {"range": f"orders!T{target_idx}", "values": [["courier_requested"]]},
                    {"range": f"orders!AA{target_idx}", "values": [[new_comment]]},  # ✅ COMMENT
                ],
            },
        ).execute()

        log.info(
            f"Order {order_id} updated: pickup_eta_at={pickup_eta_at}, "
            f"courier_state=courier_requested, comment='{new_comment}'"
        )

    except Exception:
            log.exception(f"Failed to update ETA/comment for order {order_id}")
            return

    # 8️⃣ Перечитываем строку
    rows = sheet.values().get(
        spreadsheetId=spreadsheet_id,  # ✅ ИЗ KITCHEN
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    order_row = rows[target_idx - 1]

    # 9️⃣ Вызов курьера
    success = await send_to_courier_and_persist(
        order_row=order_row,
        target_idx=target_idx,
        pickup_eta_at=pickup_eta_at,
        eta_minutes=minutes,
        kitchen=kitchen,
    )

    if not success:
        log.error(f"Failed to send order {order_id} to courier")

    # 🔟 Уведомление клиента
    buyer_user_id = int(order_row[2])

    buyer_chat_id = get_client_chat_id(
        kitchen=kitchen,
        user_id=buyer_user_id,
    )

    if buyer_chat_id:
        await context.bot.send_message(
            chat_id=buyer_chat_id,
            text=(
                "Ваш заказ принят в работу.\n"
                f"Курьер приедет примерно через {minutes} минут.\n"
                "Вы можете отслеживать доставку в боте курьерской службы."
            ),
        )
    else:
        log.info(
            "Client notification skipped: no telegram_chat_id "
            f"(user_id={buyer_user_id})"
        )

    try:
        await q.message.delete()
    except Exception:
        pass
    
    log.info(f"=== EXIT on_staff_eta: {order_id} processed ===")

async def on_staff_no_courier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat_id
    if chat_id not in STAFF_CHAT_IDS:
        return

    # kitchen + spreadsheet (важно для мульти-кухонь)
    # Парсим callback: staff:no_courier:kitchen_id:order_id
    parts = q.data.split(":")
    if len(parts) != 4:
        log.error(f"Invalid no_courier callback: {q.data}")
        return

    _, _, kitchen_id, order_id = parts

    # Получаем kitchen из callback
    from kitchen_context import require
    try:
        kitchen = require(kitchen_id)
    except Exception as e:
        log.error(f"Kitchen {kitchen_id} not found: {e}")
        return

    spreadsheet_id = kitchen.spreadsheet_id
    if not kitchen:
        log.error("on_staff_no_courier: kitchen not resolved")
        return
    spreadsheet_id = kitchen.spreadsheet_id

    _, _, order_id = q.data.split(":", 2)

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    # защита от повторных действий
    service = get_sheets_service()
    sheet = service.spreadsheets()
    rows = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
        range=ORDERS_RANGE,
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

    COL_COURIER_EXTERNAL_ID_IDX = ord("W") - ord("A")  # = 22

    order_row = rows[target_idx - 1]
    external_id = (
        order_row[COL_COURIER_EXTERNAL_ID_IDX]
        if len(order_row) > COL_COURIER_EXTERNAL_ID_IDX
        else ""
    )

    try:
        await courier_cancel_order(external_id)
    except Exception as e:
        log.warning(f"⚠️ courier cancel failed for order {order_id}: {e}")

    sheet.values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"orders!T{target_idx}", "values": [["courier_not_requested"]]},
                {"range": f"orders!U{target_idx}", "values": [[""]]},  # courier_no_reason (резерв)
            ],
        },
    ).execute()

    buyer_user_id = int(rows[target_idx - 1][2])

    buyer_chat_id = get_client_chat_id(
        kitchen=kitchen,
        user_id=buyer_user_id,
    )

    if buyer_chat_id:
        await context.bot.send_message(
            chat_id=buyer_chat_id,
            text="Ваш заказ принят. Курьер вызываться не будет.",
        )
    else:
        log.info(
            f"Client notification skipped: no telegram_chat_id "
            f"(user_id={buyer_user_id})"
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

    service = get_sheets_service()
    sheet = service.spreadsheets()
    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ORDERS_RANGE,
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

    # защита от повторных решений
    if current_status in ("courier_requested", "courier_not_requested"):
        await q.answer("Решение по курьеру уже принято", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ⬇️ ТОЛЬКО установка ожидания
    set_waiting_manual_eta(context, order_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🕒 <b>Введите дату и время прибытия курьера</b>\n\n"
            "Формат: <code>DD.MM HH:MM</code>\n"
            "Пример: <code>28.01 18:30</code>"
        ),
        parse_mode=ParseMode.HTML,
    )

    try:
        await q.message.delete()
    except Exception:
        pass


import httpx
import time

COURIER_API_BASE = os.getenv("COURIER_API_BASE", "")
API_KEY = os.getenv("API_KEY", "DEV_KEY")
COURIER_TIMEOUT  = 10

async def courier_update_order(external_id: str, patch: dict) -> dict:
    """
    Обновление заказа в курьерке (ETA, comment).
    dev-safe: всегда ok.
    """
    if not external_id:
        return {"ok": False, "error": "external_id is empty"}

    if not COURIER_API_BASE:
        return {"ok": True}

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=COURIER_TIMEOUT) as client:
        r = await client.patch(
            f"{COURIER_API_BASE}/orders/{external_id}",
            headers=headers,
            json=patch,
        )
        r.raise_for_status()
        return {"ok": True}


async def courier_cancel_order(external_id: str) -> dict:
    """
    Отмена заказа в курьерке.
    dev-safe: ok.
    """
    if not external_id:
        return {"ok": True}

    if not COURIER_API_BASE:
        return {"ok": True}

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=COURIER_TIMEOUT) as client:
        r = await client.post(
            f"{COURIER_API_BASE}/orders/{external_id}/cancel",
            headers=headers,
            json={},
        )
        r.raise_for_status()
        return {"ok": True}


# =========================
# Web API client (kitchen -> webapi)
# =========================

async def create_webapi_order(payload: dict) -> dict:
    url = f"{WEB_API_BASE_URL}/api/v1/orders"

    order_id = payload.get("order_id")
    kitchen_id = payload.get("kitchen_id")

    headers = {
        "X-API-KEY": WEB_API_KEY,
        "X-ROLE": "kitchen",
    }
    if order_id:
        headers["X-IDEMPOTENCY-KEY"] = str(order_id)

    timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)

    log.info(
        "[WEBAPI] create order order_id=%r kitchen_id=%r url=%s",
        order_id,
        kitchen_id,
        url,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        log.error(
            "[WEBAPI] create order failed %s %s",
            resp.status_code,
            resp.text,
        )
        raise RuntimeError("WEB API order create failed")

    return resp.json()

from datetime import datetime, timezone
async def send_to_courier_and_persist(
    order_row: list,
    target_idx: int,
    *,
    pickup_eta_at: str | None = None,
    eta_minutes: int | None = None,
    kitchen=None,
):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # spreadsheetId должен быть кухни, если мы ее знаем
    spreadsheet_id = None
    if kitchen is not None:
        spreadsheet_id = getattr(kitchen, "spreadsheet_id", None)
    if not spreadsheet_id:
        spreadsheet_id = SPREADSHEET_ID

    # 1) формируем payload ИСКЛЮЧИТЕЛЬНО из order_row
    payload = build_courier_payload(
        order_row,
        pickup_eta_at=pickup_eta_at,
        eta_minutes=eta_minutes,
    )

    payload["pickup_address"] = payload.get("pickup_address") or "충남 아산시 둔포면 둔포중앙로161번길 21-2"
    payload["city"] = payload.get("city") or "dunpo"

    # страховка kitchen_id для мультикухни
    if not payload.get("kitchen_id") and kitchen is not None:
        payload["kitchen_id"] = getattr(kitchen, "kitchen_id", None)

    log.error(
        "[send_to_courier_and_persist] payload built | "
        f"order_id={payload.get('order_id')} "
        f"pickup_eta_at={payload.get('pickup_eta_at')!r} "
        f"price_krw={payload.get('price_krw')!r}"
    )

    kitchen_id = payload.get("kitchen_id")
    if not kitchen_id:
        raise RuntimeError("kitchen_id missing in payload")
    
    # ✅ ДОБАВЛЕНО: Преобразование kitchen_id для WebAPI
    # WebAPI ожидает INT, а не STRING
    kitchen_id_for_webapi = None
    
    if isinstance(kitchen_id, str):
        # Извлекаем число из "kitchen_5" → 5
        if kitchen_id.startswith("kitchen_"):
            try:
                kitchen_id_for_webapi = int(kitchen_id.replace("kitchen_", ""))
            except ValueError:
                log.error(f"Invalid kitchen_id format: {kitchen_id}")
                kitchen_id_for_webapi = 1  # fallback
        else:
            # Если просто число в строке "5"
            try:
                kitchen_id_for_webapi = int(kitchen_id)
            except ValueError:
                kitchen_id_for_webapi = 1
    elif isinstance(kitchen_id, int):
        kitchen_id_for_webapi = kitchen_id
    else:
        kitchen_id_for_webapi = 1
    
    log.info(
        f"[send_to_courier_and_persist] kitchen_id conversion: "
        f"{kitchen_id!r} → {kitchen_id_for_webapi} (type={type(kitchen_id_for_webapi)})"
    )

    # 2) регистрируем заказ в Web API (best-effort, не ломает флоу)
    try:
        await create_webapi_order({
            "order_id": payload["order_id"],
            "source": "kitchen",
            "kitchen_id": kitchen_id_for_webapi,
            "client_tg_id": payload["client_tg_id"],
            "client_name": payload["client_name"],
            "client_phone": payload["client_phone"],
            "pickup_address": payload["pickup_address"],
            "delivery_address": payload["delivery_address"],
            "pickup_eta_at": payload.get("pickup_eta_at"),
            "city": payload["city"],
            "comment": payload.get("comment"),
            # ⚠️ ВАЖНО: цена берется ИЗ payload
            "price_krw": payload.get("price_krw"),
            "eta_minutes": eta_minutes,
        })
    except Exception:
        log.exception("[send_to_courier_and_persist] WebAPI create_order failed")
        # ❗️ не ломаем флоу кухни

    # 3) финальная защита перед отправкой курьеру
    if not payload.get("pickup_eta_at"):
        payload["pickup_eta_at"] = datetime.now(timezone.utc).isoformat()
        log.error(
            "[send_to_courier_and_persist] pickup_eta_at was empty -> forced now | "
            f"{payload['pickup_eta_at']}"
        )

    try:
        # 4) вызов курьерки
        log.error("[send_to_courier_and_persist] BEFORE courier_create_order")
        res = await courier_create_order(payload)
        log.error(f"[send_to_courier_and_persist] AFTER courier_create_order res={res!r}")

        if res.get("status") != "ok":
            raise RuntimeError(f"courier response not ok: {res!r}")

        external_id = res.get("delivery_order_id") or ""

        # 5) фиксируем успех в Sheets
        sheet.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"orders!W{target_idx}", "values": [[external_id]]},
                    {"range": f"orders!T{target_idx}", "values": [["courier_requested"]]},
                    {"range": f"orders!X{target_idx}", "values": [["ok"]]},
                    {"range": f"orders!Y{target_idx}", "values": [[""]]},
                    {
                        "range": f"orders!Z{target_idx}",
                        "values": [[datetime.now(timezone.utc).isoformat()]],
                    },
                ],
            },
        ).execute()

        log.error(
            "[send_to_courier_and_persist] SUCCESS | "
            f"order_idx={target_idx} external_id={external_id!r}"
        )
        return True

    except Exception as e:
        log.exception(
            "[send_to_courier_and_persist] EXCEPTION while sending to courier"
        )

        sheet.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
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
        range=ORDERS_RANGE,
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

    if q.message.chat_id not in ADMIN_IDS:
        return

    if q.data != "owner:commission_paid_apply":
        return
    
    q = update.callback_query
    await q.answer()

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ORDERS_RANGE,
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

            if len(r) > 1 and r[1]:
                dates.append(r[1])  # created_at

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
                ",".join(str(x) for x in ADMIN_IDS),
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
            "⚠️ <b>Подтверждение закрытия периода</b>\n\n"
            f"📦 Заказов: <b>{orders_count}</b>\n"
            f"💰 Сумма: <b>{_fmt_money(total_amount)}</b>\n\n"
            "Закрыть период и отметить оплату?"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_owner_paid_confirm(),
    )

    try:
        await q.message.delete()
    except Exception:
        pass

async def on_owner_commission_paid_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.message.chat_id not in ADMIN_IDS:
        return

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    orders_count = 0
    total_amount = 0

    for r in rows[1:]:
        if len(r) > 28 and r[28] == "unpaid":
            orders_count += 1
            try:
                total_amount += int(r[27])
            except Exception:
                pass

    if orders_count == 0:
        await q.answer("Нет задолженности", show_alert=True)
        return

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "⚠️ <b>Подтверждение закрытия периода</b>\n\n"
                f"📦 Заказов: <b>{orders_count}</b>\n"
                f"💰 Сумма: <b>{_fmt_money(total_amount)}</b>\n\n"
                "Закрыть период и отметить оплату?"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_owner_paid_confirm(),
        )

    try:
        await q.message.delete()
    except Exception:
        pass

async def on_owner_commission_paid_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Отменено")

    try:
        await q.message.delete()
    except Exception:
        pass

def get_user_profile(
    kitchen: KitchenContext,
    user_id: int,
) -> dict | None:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = (
        sheet.values()
        .get(
            spreadsheetId=kitchen.spreadsheet_id,
            range="users!A:G",
        )
        .execute()
        .get("values", [])
    )

    uid = str(user_id)

    for r in rows:
        if not r:
            continue

        # на случай, если первая строка это заголовки
        if r[0].strip().lower() in ("user_id", "userid", "id"):
            continue

        if r[0] == uid:
            return {
                "name": r[4] if len(r) > 4 else "",
                "phone": r[5] if len(r) > 5 else "",
            }

    return None

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
            range=ORDERS_RANGE,
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

        buyer_user_id = int(rows[target_idx - 1][2])

        buyer_chat_id = get_client_chat_id(
            kitchen=kitchen,
            user_id=buyer_user_id,
        )

        if buyer_chat_id:
            await context.bot.send_message(
                chat_id=buyer_chat_id,
                text=(
                    "Ваш заказ принят.\n"
                    "Кухня приступила к приготовлению."
                ),
            )
        else:
            log.info(
                f"Client notification skipped: no telegram_chat_id "
                f"(user_id={buyer_user_id})"
            )

        context.user_data.pop("waiting_manual_eta", None)
        await update.message.reply_text("✅ Время курьера сохранено.")
        return

    # ===== STAFF CHECK (MULTI-KITCHEN) =====
    from kitchen_context import _REGISTRY

    chat_id = update.effective_chat.id
    is_staff = False

    for kitchen in _REGISTRY.values():
        if chat_id == kitchen.owner_chat_id or chat_id in kitchen.staff_chat_ids:
            is_staff = True
            break

    if not is_staff:
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

_KITCHEN_CACHE = {"address": None, "loaded_at": 0}

def get_kitchen_address_cached(
    *,
    kitchen: KitchenContext,
    ttl: int = 300,
) -> str | None:
    now = time.time()

    cache = _KITCHEN_CACHE.get(kitchen.kitchen_id)
    if cache and cache.get("address") and now - cache["loaded_at"] < ttl:
        return cache["address"]

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
        range="kitchen!A:B",
    ).execute().get("values", [])

    for r in rows:
        if len(r) >= 2 and r[0] == "address":
            _KITCHEN_CACHE[kitchen.kitchen_id] = {
                "address": r[1],
                "loaded_at": now,
            }
            return r[1]

    return None
# -------------------------
# WEBAPP
# -------------------------
async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.web_app_data:
        return

    raw_data = update.message.web_app_data.data
    log.info("📦 WEBAPP RAW DATA: %s", raw_data)

    try:
        data = json.loads(raw_data)
    except Exception:
        log.exception("❌ WEBAPP JSON PARSE ERROR")
        return

    if data.get("type") != "webapp_checkout":
        log.debug("WEBAPP DATA IGNORED type=%s", data.get("type"))
        return

    order_id = data.get("order_id")
    kitchen_id = data.get("kitchen_id")

    log.critical(
        "🔥 WEBAPP CHECKOUT RECEIVED order=%s kitchen=%s",
        order_id,
        kitchen_id,
    )

    # НИЧЕГО НЕ ДЕРГАЕМ
    # НИ sync
    # НИ notify
    # Sheets = источник истины

    await update.message.reply_text(
        "✅ Заказ принят и передан кухне.\n"
        "Мы уже уведомляем персонал ⏳"
    )
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


def set_product_description(
    kitchen: KitchenContext,
    product_id: str,
    description: str,
) -> bool:

    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
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
        spreadsheetId=kitchen.spreadsheet_id,
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



def set_product_available(
    kitchen: "KitchenContext",
    product_id: str,
    available: bool,
) -> bool:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
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
        spreadsheetId=kitchen.spreadsheet_id,
        range=f"products!D{row_index}",
        valueInputOption="RAW",
        body={"values": [["TRUE" if available else "FALSE"]]},
    ).execute()

    return True

def set_product_photo(
    kitchen: "KitchenContext",
    product_id: str,
    file_id: str,
) -> bool:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=kitchen.spreadsheet_id,
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
        spreadsheetId=kitchen.spreadsheet_id,
        range=f"products!F{row_index}",
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
    from marketplace_handlers import get_active_kitchen

    kitchen = get_active_kitchen(context)

    products = read_products_from_sheets(
        spreadsheet_id=kitchen.spreadsheet_id
    )
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
    from marketplace_handlers import get_active_kitchen
    kitchen = get_active_kitchen(context)

    products = [
        p for p in read_products_from_sheets(
            spreadsheet_id=kitchen.spreadsheet_id
        )
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
            f"Цена: {_fmt_money(p['owner_price'])}\n"
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

#===========HANDLE SCREENSHOTS======================#


def build_payment_proof_button(payment_file_id: str):
    if not payment_file_id:
        return None

    if not payment_file_id.startswith("upload:"):
        return None

    upload_id = payment_file_id.split("upload:", 1)[1]

    WEB_API_URL = os.getenv(
        "WEB_API_URL",
        "https://web-api-integration-production.up.railway.app",
    )

    proof_url = f"{WEB_API_URL}/api/v1/uploads/payment-proof/{upload_id}"

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Открыть чек", url=proof_url)]
    ])

def build_payment_proof_kb(payment_file_id: str):
    """
    Возвращает клавиатуру с кнопкой 'Открыть чек' или None.
    Ожидаем, что payment_file_id будет типа 'upload:<upload_id>'.
    """
    log.error(f"[PAYMENT_KB] WEB_API_URL={os.getenv('WEB_API_URL')}")
    log.error(f"[PAYMENT_KB] payment_file_id={payment_file_id}")

    if not payment_file_id:
        return None

    s = str(payment_file_id).strip()
    if not s.startswith("upload:"):
        return None

    upload_id = s.split("upload:", 1)[1].strip()
    if not upload_id:
        return None

    web_api_url = os.getenv("WEB_API_URL", "").rstrip("/")
    if not web_api_url:
        return None

    url = f"{web_api_url}/api/v1/uploads/payment-proof/{upload_id}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Открыть чек", url=url)]
    ])

def merge_inline_keyboards(*kbs):
    """
    Склеивает несколько InlineKeyboardMarkup в одну.
    None игнорируем.
    """
    rows = []
    for kb in kbs:
        if not kb:
            continue
        try:
            rows.extend(kb.inline_keyboard or [])
        except Exception:
            pass

    return InlineKeyboardMarkup(rows) if rows else None

async def on_open_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    if not data.startswith("open_proof:"):
        return

    await query.answer()

    order_id = data.split(":", 1)[1]

    # --- читаем заказ из Sheets ---
    service = get_sheets_service()

    rows = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=context.bot_data["spreadsheet_id"],
            range=ORDERS_RANGE,
        )
        .execute()
        .get("values", [])
    )

    order_row = None
    for r in rows[1:]:
        if r and r[0] == order_id:
            order_row = r
            break

    if not order_row:
        await query.message.reply_text("❌ Чек не найден")
        return

    payment_file_id = order_row[8] if len(order_row) > 8 else ""
    if not payment_file_id.startswith("upload:"):
        await query.message.reply_text("❌ Чек не найден")
        return

    upload_id = payment_file_id.split("upload:", 1)[1]

    # --- идем в Web API ---
    web_api_url = os.getenv("WEB_API_URL", "").rstrip("/")
    api_key = os.getenv("WEB_API_KEY")

    if not web_api_url or not api_key:
        await query.message.reply_text("❌ Web API не настроен")
        return

    file_url = f"{web_api_url}/api/v1/uploads/payment-proof/{upload_id}"

    headers = {
        "x-api-key": api_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, headers=headers) as resp:
                if resp.status != 200:
                    await query.message.reply_text("❌ Не удалось получить чек")
                    return

                image_bytes = await resp.read()

        await context.bot.send_photo(
            chat_id=query.message.chat.id,
            photo=image_bytes,
            caption=f"🧾 Чек по заказу {order_id}",
        )

    except Exception as e:
        await query.message.reply_text("❌ Ошибка при загрузке чека")

#===========NOTIFY STAFF======================#

async def notify_staff(bot, kitchen, order_id: str):
    log.error("🔥🔥🔥 notify_staff CALLED")

    # --- защита ---
    if kitchen is None:
        log.error("notify_staff called without kitchen")
        return None

    order_id = str(order_id)

    # --- получатели ---
    staff_chat_ids = set()

    try:
        if getattr(kitchen, "staff_chat_ids", None):
            staff_chat_ids.update(kitchen.staff_chat_ids)
    except Exception:
        pass

    owner_chat_id = getattr(kitchen, "owner_chat_id", None)
    if owner_chat_id:
        staff_chat_ids.add(owner_chat_id)

    if not staff_chat_ids:
        log.warning(f"no staff recipients for kitchen={kitchen.id}")
        return None

    # --- sheets ---
    spreadsheet_id = kitchen.spreadsheet_id
    service = get_sheets_service()

    rows = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=ORDERS_RANGE,
        )
        .execute()
        .get("values", [])
    )

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

    # --- парс заказа ---
    order_id        = order_row[0]
    created_at      = order_row[1] if len(order_row) > 1 else ""
    buyer_chat_id   = order_row[2] if len(order_row) > 2 else ""
    items           = order_row[4] if len(order_row) > 4 else ""
    total           = int(order_row[5]) if len(order_row) > 5 and str(order_row[5]).isdigit() else 0
    kind            = order_row[6] if len(order_row) > 6 else ""
    comment         = order_row[7] if len(order_row) > 7 else ""
    payment_file_id = order_row[8] if len(order_row) > 8 else ""
    status          = order_row[9] if len(order_row) > 9 else ""

    address         = order_row[13] if len(order_row) > 13 else ""
    delivery_fee    = int(order_row[14]) if len(order_row) > 14 and str(order_row[14]).isdigit() else 0

    if status not in ("pending", "created"):
        return None

    # --- покупатель ---
    buyer_name = ""
    buyer_phone = ""

    users = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range="users!A:G",
        )
        .execute()
        .get("values", [])
    )

    for u in users:
        if u and u[0] == buyer_chat_id:
            buyer_name = u[4] if len(u) > 4 else ""
            buyer_phone = u[5] if len(u) > 5 else ""
            break

    # --- текст ---
    address_block = (
        f"\n📍 <b>Адрес:</b>\n<code>{address}</code>\n"
        if address
        else ""
    )

    delivery_line = ""
    if kind == "Доставка":
        delivery_line = (
            "🚚 <b>Доставка:</b> бесплатно\n"
            if delivery_fee == 0
            else f"🚚 <b>Доставка:</b> {_fmt_money(delivery_fee)}\n"
        )

    caption = (
        "🧨 У ВАС НОВЫЙ ЗАКАЗ!\n\n"
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

    # --- отправка ---
    first_msg = None

    for staff_id in staff_chat_ids:
        try:
            proof_kb = build_payment_proof_kb(payment_file_id)

            reply_markup = merge_inline_keyboards(
                proof_kb,
                kb_staff_order(order_id),
            )

            msg = await bot.send_message(
                chat_id=staff_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
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
    kitchen: KitchenContext,
    kind_label: str,
    comment: str,
    address: str | None = None,
    delivery_price_krw: int | None = None,
) -> str:
    kind = "delivery" if kind_label == "Доставка" else "pickup"

    subtotal = cart_total(cart, kitchen)

    # Используем цену из геокодинга, если есть
    if delivery_price_krw is not None:
        delivery_fee = delivery_price_krw
    else:
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
        f"{cart_text(cart, kitchen)}\n\n"
        f"{delivery_block}"
        f"💰 <b>Итого к оплате: {_fmt_money(total)}</b>\n\n"
        f"Способ: <b>{kind_label}</b>\n"
        f"{address_block}"
        f"Комментарий: <b>{comment or '—'}</b>\n\n"
        "На этом этапе необходимо произвести оплату на наш тонжан и прикрепить скриншот ⬇️"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # 🔗 пробрасываем render_home в marketplace
    app.bot_data["render_home"] = render_home

    log.info("### BOT STARTED ###")
    # -------- Marketplace Handlers --------

    from kitchen_context import list_kitchens, get, load_registry
    from webapp_orders_sync import orders_job  # ✅ ОДНА job

    log.info("### REGISTERING ORDERS JOBS FOR ALL KITCHENS ###")

    load_registry()
    all_kitchen_ids = list_kitchens()

    for kitchen_id in all_kitchen_ids:
        try:
            kitchen = get(kitchen_id)
            if not kitchen or kitchen.status != "active":
                log.warning(f"Skip inactive kitchen: {kitchen_id}")
                continue

            if not app.job_queue:
                log.warning(
                    f"JobQueue not available, skip orders job for {kitchen_id}"
                )
                continue
            
            # ===== ОДНА JOB: orders_job (sync + notify) =====
            app.job_queue.run_repeating(
                orders_job,
                interval=5,
                first=1,
                data={
                    "spreadsheet_id": kitchen.spreadsheet_id,
                    "kitchen_id": kitchen_id,
                },
                name=f"orders:{kitchen_id}",
            )
            
            log.info(f"✅ Registered orders job for {kitchen_id}")
            
        except Exception as e:
            log.error(f"❌ Failed to register job for {kitchen_id}: {e}")

    log.info("### ORDERS JOBS REGISTRATION COMPLETE ###")


   # -------- Marketplace Handlers --------
    app.add_handler(
        CallbackQueryHandler(
            marketplace_back,
            pattern=r"^market:back$"
        )
    )
    app.add_handler(CommandHandler("market", marketplace_start))

    app.add_handler(
        CallbackQueryHandler(
            marketplace_select_kitchen,
            pattern=r"^marketplace:kitchen:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            marketplace_callback,
            pattern=r"^market:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            on_open_payment_proof,
            pattern=r"^open_proof:"
        )
    )
    # -------- COMMANDS --------
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("catalog", catalog_cmd))
    app.add_handler(CommandHandler("dash", dash_cmd))

    # -------- BUYER TEXT (checkout replies) --------
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_checkout_reply
        ),
        group=1
    )

    # -------- WEB API --------
    app.add_handler(CallbackQueryHandler(on_staff_eta, pattern=r"^staff:eta:\d+:"))
    app.add_handler(CallbackQueryHandler(on_staff_no_courier, pattern=r"^staff:no_courier:"))
    app.add_handler(CallbackQueryHandler(on_staff_eta_manual_click, pattern=r"^staff:eta_manual:"))
    app.add_handler(CallbackQueryHandler(on_owner_commission_paid, pattern=r"^owner:commission_paid$"))
    app.add_handler(CallbackQueryHandler(on_staff_courier_retry, pattern=r"^staff:courier_retry:"))
    app.add_handler(CallbackQueryHandler(on_owner_commission_paid_confirm, pattern=r"^owner:commission_paid_confirm$"))
    app.add_handler(CallbackQueryHandler(on_owner_commission_paid, pattern=r"^owner:commission_paid_apply$"))
    app.add_handler(CallbackQueryHandler(on_owner_commission_paid_cancel, pattern=r"^owner:commission_paid_cancel$"))

    # -------- CALLBACKS (ВСЕ КНОПКИ) --------
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

    # -------- BUYER PHOTO (payment proof) --------
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE),
            on_buyer_payment_photo
        )
    )

    # -------- STAFF --------
    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            on_staff_photo
        )
    )

    # -------- STAFF TEXT --------
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_staff_text
        ),
        group=10
    )

    app.bot_data["SHEETS_SERVICE"] = None

    register_broadcast_handlers(app)

    log.info("### START POLLING ###")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
        
# -------- BUYER PHOTO (payment proof) --------
print("### MAIN FILE REACHED END ###")
    
def get_product_by_id(product_id: str, kitchen):
    for p in read_products_from_sheets(kitchen):
        if p["product_id"] == product_id:
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