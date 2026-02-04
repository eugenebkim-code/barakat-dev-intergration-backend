# staff_callbacks.py

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from staff_decision import handle_staff_decision
from keyboards_staff import kb_staff_pickup_eta, kb_staff_only_check

log = logging.getLogger("STAFF_CALLBACKS")


async def staff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    staff_user = query.from_user

    data = query.data or ""
    log.info(f"STAFF CALLBACK DATA: {data}")

    try:
        await query.answer()
    except Exception:
        pass

    parts = data.split(":")
    if len(parts) < 4:
        log.error(f"Bad callback data format: {data}")
        return

    prefix, action, order_id, kitchen_id = parts[:4]

    from kitchen_context import _REGISTRY
    kitchen = _REGISTRY.get(kitchen_id)

    if not kitchen:
        await query.answer("Кухня не найдена", show_alert=True)
        return

    allowed_ids = {kitchen.owner_chat_id} | kitchen.staff_chat_ids

    if staff_user.id not in allowed_ids:
        await query.answer("Недостаточно прав", show_alert=True)
        log.warning(
            f"Unauthorized staff callback | "
            f"user={staff_user.id} kitchen={kitchen_id}"
        )
        return

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
                reply_markup=kb_staff_only_check(order_id),
            )
        else:
            base_caption = msg.caption or ""
            await msg.edit_caption(
                caption=base_caption + f"\n\n<b>{suffix}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_staff_only_check(order_id),
            )

        log.info(f"Staff message updated for order {order_id}")

    except Exception:
        log.exception("Failed to edit staff message")

    # если отклонено — просто удаляем сообщение
    if decision != "approved":
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
  