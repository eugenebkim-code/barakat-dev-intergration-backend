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

    if len(parts) < 3:
        log.error(f"Bad callback data format: {data}")
        return

    prefix = parts[0]
    action = parts[1]
    order_id = parts[2]
    kitchen_id = parts[3] if len(parts) >= 4 else None

    from kitchen_context import _REGISTRY

    # если kitchen_id не пришел — пытаемся восстановить по order_id
    if not kitchen_id:
        for k in _REGISTRY.values():
            if not k.spreadsheet_id:
                await query.answer("Не найден spreadsheet кухни", show_alert=True)
                return

        if not kitchen_id:
            log.error(f"Cannot resolve kitchen_id for order {order_id}")
            await query.answer("Ошибка: кухня не определена", show_alert=True)
            return

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

    try:
        msg = query.message

        if msg.text:
            await msg.edit_text(
                text=msg.text + f"\n\n<b>{suffix}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_staff_only_check(order_id),
            )
        else:
            await msg.edit_caption(
                caption=(msg.caption or "") + f"\n\n<b>{suffix}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_staff_only_check(order_id),
            )

        log.info(f"Staff message updated for order {order_id}")

    except Exception:
        log.exception("Failed to edit staff message")

    if decision != "approved":
        return

    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Через сколько должен приехать курьер?",
            reply_markup=kb_staff_pickup_eta(order_id, kitchen_id),
        )
        log.info(f"✅ ETA buttons sent: order={order_id}, kitchen={kitchen_id}")
    except Exception:
        log.exception(f"failed to send ETA buttons for order {order_id}")