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
    context,
    bot: Bot,
    order_id: str,
    decision: str,
    staff_user_id: int,
    staff_username: str | None,
):
    #\"\"\"
    #Единственная точка обработки решения стафа.
    #Ищет заказ во ВСЕХ кухнях.
    #\"\"\"
    from datetime import datetime, timezone
    from kitchen_context import _REGISTRY
    from sheets_repo import get_sheets_service, update_order_cells
    from config import ORDERS_RANGE
    import logging
    
    log = logging.getLogger("STAFF_DECISION")
    now = datetime.now(timezone.utc)

    # 1️⃣ Ищем заказ во ВСЕХ кухнях
    spreadsheet_id = None
    row_idx = None
    order_row = None
    kitchen_id = None
    
    service = get_sheets_service()
    sheet = service.spreadsheets()
    
    for kid, kctx in _REGISTRY.items():
        try:
            rows = sheet.values().get(
                spreadsheetId=kctx.spreadsheet_id,
                range=ORDERS_RANGE,
            ).execute().get("values", [])
            
            for i, r in enumerate(rows[1:], start=2):
                if r and r[0] == order_id:
                    spreadsheet_id = kctx.spreadsheet_id
                    kitchen_id = kid
                    row_idx = i
                    order_row = r
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
    
    # Данные из строки
    created_at = order_row[1] if len(order_row) > 1 else ""
    user_id = order_row[2] if len(order_row) > 2 else ""

    if not created_at or not user_id:
        log.error(f"Broken order data for {order_id}")
        return

    created_dt = datetime.fromisoformat(created_at)
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)

    reaction_seconds = int((now - created_dt).total_seconds())

    # 2️⃣ Обновляем в ПРАВИЛЬНОЙ таблице
    updates = {
        "status": decision,
        "handled_at": now.isoformat(),
        "handled_by": staff_username or str(staff_user_id),
        "reaction_seconds": reaction_seconds,
    }

    update_order_cells(row_idx, updates, spreadsheet_id=spreadsheet_id)

    # 3️⃣ Уведомляем клиента
    client_chat_id = int(user_id)

    if decision == "approved":
        client_text = (
            "Ваш заказ принят в работу 👍\\n"
            "Мы начали обработку и свяжемся с вами при необходимости."
        )
    else:
        client_text = (
            "По вашему заказу возникли сложности.\\n"
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
        f"kitchen={kitchen_id}, reaction_seconds={reaction_seconds}"
    )