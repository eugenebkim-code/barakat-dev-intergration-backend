# staff_callbacks.py

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from staff_decision import handle_staff_decision
from config import STAFF_CHAT_IDS

log = logging.getLogger("STAFF_CALLBACKS")


async def staff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    log.info(f"STAFF CALLBACK DATA: {data}")

    # защита: только стаф
    staff_user = query.from_user
    if staff_user.id not in STAFF_CHAT_IDS:
        await query.answer("Недостаточно прав", show_alert=True)
        log.warning(f"Unauthorized staff callback from {staff_user.id}")
        return

    await query.answer()

    # формат: staff:approve:{order_id} | staff:reject:{order_id}
    try:
        _, action, order_id = data.split(":", 2)
    except ValueError:
        log.error(f"Bad callback data format: {data}")
        return

    decision = "approved" if action == "approve" else "rejected"

    staff_user_id = staff_user.id
    staff_username = staff_user.username

    log.info(
        f"Staff decision received | "
        f"order_id={order_id} decision={decision} "
        f"staff_user_id={staff_user_id} username={staff_username}"
    )

    # 1️⃣ бизнес-логика
    try:
        await handle_staff_decision(
            bot=context.bot,
            order_id=order_id,
            decision=decision,
            staff_user_id=staff_user_id,
            staff_username=staff_username,
        )
        log.info(f"handle_staff_decision OK for order {order_id}")
    except Exception:
        log.exception(f"handle_staff_decision FAILED for order {order_id}")
        await query.answer("Ошибка обработки заказа", show_alert=True)
        return

    # 2️⃣ обновляем сообщение стафа
    try:
        suffix = "Принят в работу ✅" if decision == "approved" else "Отклонен ❌"
        await query.edit_message_text(
            text=query.message.text + f"\n\n<b>{suffix}</b>",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"Staff message updated for order {order_id}")
    except Exception:
        log.exception("Failed to edit staff message")
    # 2️⃣ удаляем сообщение стафа после решения
    try:
        await query.message.delete()
        log.info(f"Staff message deleted for order {order_id}")
    except Exception:
        log.exception("Failed to delete staff message")
