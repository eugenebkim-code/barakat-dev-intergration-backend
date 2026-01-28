# keyboards_staff.py

from telegram import InlineKeyboardMarkup, InlineKeyboardButton


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
            InlineKeyboardButton(
                "🕒 Указать дату и время",
                callback_data=f"staff:eta_manual:{order_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Не вызывать курьера",
                callback_data=f"staff:no_courier:{order_id}",
            ),
        ],
    ])
