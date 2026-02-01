# staff_decision.py

from datetime import datetime, timezone
import logging

from telegram import Bot
from telegram.constants import ParseMode

from sheets_repo import (
    find_order_row_by_id,
    update_order_cells,
)

log = logging.getLogger("STAFF_DECISION")


async def handle_staff_decision(
    *,
    bot: Bot,
    order_id: str,
    decision: str,            # "approved" | "rejected"
    staff_user_id: int,
    staff_username: str | None,
):
    """
    Единственная точка обработки решения стафа
    """

    now = datetime.now(timezone.utc)

    # 1️⃣ находим заказ
    from utils_spreadsheet import get_active_spreadsheet_id

    spreadsheet_id = get_active_spreadsheet_id(context)
    row_idx, order = find_order_row_by_id(
        order_id,
        spreadsheet_id=spreadsheet_id,
    )
    if not row_idx or not order:
        log.error(f"Order not found: {order_id}")
        return
    
    now = datetime.now(timezone.utc)

    created_at = order.get("created_at")
    user_id = order.get("user_id")

    if not created_at or not user_id:
        log.error(f"Broken order data for {order_id}: {order}")
        return

    created_dt = datetime.fromisoformat(created_at)

    # если время без timezone — считаем, что это UTC
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)

    reaction_seconds = int((now - created_dt).total_seconds())

    # 2️⃣ обновляем Sheets
    updates = {
        "status": decision,
        "handled_at": now.isoformat(),
        "handled_by": staff_username or str(staff_user_id),
        "reaction_seconds": reaction_seconds,
    }

    update_order_cells(row_idx, updates)

    # 3️⃣ уведомляем клиента
    client_chat_id = int(user_id)

    if decision == "approved":
        client_text = (
            "Ваш заказ принят в работу 👍\n"
            "Мы начали обработку и свяжемся с вами при необходимости."
        )
    else:
        client_text = (
            "По вашему заказу возникли сложности.\n"
            "Мы свяжемся с вами в ближайшее время для уточнения деталей."
        )

    try:
        await bot.send_message(
            chat_id=client_chat_id,
            text=client_text,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        log.exception(f"Failed to notify client {client_chat_id}")

    log.info(
        f"Order {order_id} handled: {decision}, "
        f"reaction_seconds={reaction_seconds}"
    )
