# staff_decision.py

from datetime import datetime, timezone
import logging

from telegram import Bot
from telegram.constants import ParseMode

log = logging.getLogger("STAFF_DECISION")

log = logging.getLogger("STAFF_DECISION")

async def handle_staff_decision(
    *,
    context,
    bot: Bot,
    order_id: str,
    decision: str,
    staff_user_id: int,
    staff_username: str | None,
):
    """
    Единственная точка обработки решения стафа.
    Ищет заказ во ВСЕХ кухнях.
    """

    from datetime import datetime, timezone
    import logging

    from kitchen_context import _REGISTRY
    from sheets_repo import get_sheets_service, update_order_cells
    from config import ORDERS_RANGE
    from telegram.constants import ParseMode

    log = logging.getLogger("STAFF_DECISION")
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 1️⃣ Поиск заказа во всех кухнях
    # ------------------------------------------------------------------

    spreadsheet_id = None
    row_idx = None
    order_row = None
    kitchen_id = None

    service = get_sheets_service()
    sheets_api = service.spreadsheets()

    for kid, kitchen in _REGISTRY.items():
        if not kitchen or not kitchen.spreadsheet_id:
            continue

        try:
            rows = (
                sheets_api.values()
                .get(
                    spreadsheetId=kitchen.spreadsheet_id,
                    range=ORDERS_RANGE,
                )
                .execute()
                .get("values", [])
            )

            for i, row in enumerate(rows[1:], start=2):
                if row and row[0] == order_id:
                    spreadsheet_id = kitchen.spreadsheet_id
                    kitchen_id = kid
                    row_idx = i
                    order_row = row
                    break

            if spreadsheet_id:
                break

        except Exception as e:
            log.warning(f"Failed to search order in kitchen {kid}: {e}")
            continue

    if not spreadsheet_id or not row_idx or not order_row:
        log.error(
            f"Order not found: {order_id}. "
            f"Checked kitchens: {list(_REGISTRY.keys())}"
        )
        return

    log.info(
        f"✅ Order {order_id} found in kitchen {kitchen_id} "
        f"(spreadsheet={spreadsheet_id}, row={row_idx})"
    )

    # ------------------------------------------------------------------
    # 2️⃣ Валидация данных заказа
    # ------------------------------------------------------------------

    created_at = order_row[1] if len(order_row) > 1 else ""
    user_id = order_row[2] if len(order_row) > 2 else ""

    if not created_at or not user_id:
        log.error(f"Broken order data for {order_id}")
        return

    try:
        created_dt = datetime.fromisoformat(created_at)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
    except Exception:
        log.error(f"Invalid created_at format for order {order_id}: {created_at}")
        return

    reaction_seconds = int((now - created_dt).total_seconds())

    # ------------------------------------------------------------------
    # 3️⃣ Обновление заказа в правильной таблице
    # ------------------------------------------------------------------

    updates = {
        "status": decision,
        "handled_at": now.isoformat(),
        "handled_by": staff_username or str(staff_user_id),
        "reaction_seconds": reaction_seconds,
    }

    try:
        update_order_cells(
            row_idx,
            updates,
            spreadsheet_id=spreadsheet_id,
        )
    except Exception:
        log.exception(f"Failed to update order cells for {order_id}")
        return

    # ------------------------------------------------------------------
    # 4️⃣ Уведомление клиента
    # ------------------------------------------------------------------

    try:
        client_chat_id = int(user_id)
    except Exception:
        log.error(f"Invalid client chat id for order {order_id}: {user_id}")
        return

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

    # ------------------------------------------------------------------
    # 5️⃣ Лог финального состояния
    # ------------------------------------------------------------------

    log.info(
        f"Order {order_id} handled: {decision}, "
        f"kitchen={kitchen_id}, reaction_seconds={reaction_seconds}"
    )