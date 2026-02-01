# staff_callbacks.py

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import (
    BOT_TOKEN,
    OWNER_CHAT_ID_INT,
    ADMIN_CHAT_ID_INT,
    STAFF_CHAT_IDS,
    SPREADSHEET_ID,
    ORDERS_RANGE,   # 👈 ВОТ ЭТО ДОБАВЛЯЕМ
)
from sheets_repo import get_sheets_service
from staff_decision import handle_staff_decision
from keyboards_staff import kb_staff_pickup_eta

log = logging.getLogger("STAFF_CALLBACKS")


async def staff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    log.info(f"STAFF CALLBACK DATA: {data}")

    staff_user = query.from_user
    if staff_user.id not in STAFF_CHAT_IDS:
        try:
            await query.answer("Недостаточно прав", show_alert=True)
        except Exception:
            pass
        log.warning(f"Unauthorized staff callback from {staff_user.id}")
        return

    try:
        await query.answer()
    except Exception:
        pass

    parts = data.split(":")

    if len(parts) < 3:
        log.error(f"Bad callback data format: {data}")
        return

    prefix = parts[0]
    action = parts[1]
    order_id = parts[2]

    # принимаем ТОЛЬКО решение стафа
    if prefix != "staff" or action not in ("approve", "reject"):
        log.info(f"Skip non-staff-decision callback: {data}")
        return

    decision = "approved" if action == "approve" else "rejected"

    log.info(
        "Staff decision received | "
        f"order_id={order_id} decision={decision} "
        f"staff_user_id={staff_user.id} username={staff_user.username}"
    )

    # 1) основная бизнес-логика (статусы, метрики, уведомление клиента)
    try:
        q = update.callback_query
        staff_user = q.from_user

        await handle_staff_decision(
            context=context,
            bot=context.bot,
            order_id=order_id,
            decision=decision,
            staff_user_id=staff_user.id,
            staff_username=staff_user.username,
        )
        log.info(f"handle_staff_decision OK for order {order_id}")
    except Exception:
        log.exception(f"handle_staff_decision FAILED for order {order_id}")
        try:
            await query.answer("Ошибка обработки заказа", show_alert=True)
        except Exception:
            pass
        return

    suffix = "Принят в работу ✅" if decision == "approved" else "Отклонен ❌"

    # 2) обновляем сообщение стафа
    try:
        msg = query.message

        if msg.text:
            base_text = msg.text
            await msg.edit_text(
                text=base_text + f"\n\n<b>{suffix}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=msg.reply_markup,
            )
        else:
            base_caption = msg.caption or ""
            await msg.edit_caption(
                caption=base_caption + f"\n\n<b>{suffix}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=msg.reply_markup,
            )

        log.info(f"Staff message updated for order {order_id}")

    except Exception:
        log.exception("Failed to edit staff message")

    # если отклонено — просто удаляем сообщение
    if decision != "approved":
        try:
            await query.message.delete()
            log.info(f"Staff message deleted for order {order_id} (rejected)")
        except Exception:
            log.exception("Failed to delete staff message (rejected)")
        return
    
     # 3) approved → фиксируем ожидание ETA и отправляем кнопки
    
    kitchen_id = None
    order_row = None
    target_idx = None
    
    # Ищем заказ во ВСЕХ кухнях
    from kitchen_context import _REGISTRY
    
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
                    kitchen_id = kid
                    order_row = r
                    target_idx = i
                    break
            
            if kitchen_id:
                break
                
        except Exception as e:
            log.warning(f"Failed to search in kitchen {kid}: {e}")
            continue
    
    if not kitchen_id or not order_row or not target_idx:
        log.error(
            f"order {order_id} not found in any kitchen. "
            f"Checked: {list(_REGISTRY.keys())}"
        )
        return
    
    log.info(
        f"✅ Order {order_id} found in kitchen {kitchen_id}, row {target_idx}"
    )
    
    # Обновляем статус в ПРАВИЛЬНОЙ таблице
    try:
        sheet.values().update(
            spreadsheetId=_REGISTRY[kitchen_id].spreadsheet_id,
            range=f"orders!T{target_idx}",
            valueInputOption="RAW",
            body={"values": [["courier_pending_eta"]]},
        ).execute()

        log.info(
            f"order {order_id} moved to courier_pending_eta "
            f"(kitchen={kitchen_id}, idx={target_idx})"
        )
    except Exception:
        log.exception("Failed to update courier_pending_eta state")
        return

    # 4) Отправляем кнопки ETA с kitchen_id
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Через сколько должен приехать курьер?",
            reply_markup=kb_staff_pickup_eta(order_id, kitchen_id),
        )
        log.info(f"✅ ETA buttons sent: order={order_id}, kitchen={kitchen_id}")
    except Exception:
        log.exception(f"failed to send ETA buttons for order {order_id}")

    # 5) Удаляем сообщение
    try:
        await query.message.delete()
        log.info(f"Staff message deleted for order {order_id} (approved)")
    except Exception:
        log.exception("Failed to delete staff message (approved)")