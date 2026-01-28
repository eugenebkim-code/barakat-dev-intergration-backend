# staff_callbacks.py

import logging
import json
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import STAFF_CHAT_IDS, SPREADSHEET_ID
from sheets_repo import get_sheets_service
from staff_decision import handle_staff_decision

# Эти функции у нас уже есть в проекте.
# ВАЖНО: не импортируем main.py (чтобы не словить циклический импорт).
from courier_payload import build_courier_payload  # если у нас нет файла, смотри примечание ниже
from courier_api import courier_create_order       # если у нас нет файла, смотри примечание ниже
from keyboards_staff import kb_staff_pickup_eta          # если у нас нет файла, смотри примечание ниже

log = logging.getLogger("STAFF_CALLBACKS")


async def staff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    log.info(f"STAFF CALLBACK DATA: {data}")

    # защита: только стаф
    staff_user = query.from_user
    if staff_user.id not in STAFF_CHAT_IDS:
        try:
            await query.answer("Недостаточно прав", show_alert=True)
        except Exception:
            pass
        log.warning(f"Unauthorized staff callback from {staff_user.id}")
        return

    # отвечаем на callback сразу
    try:
        await query.answer()
    except Exception:
        pass

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
        "Staff decision received | "
        f"order_id={order_id} decision={decision} "
        f"staff_user_id={staff_user_id} username={staff_username}"
    )

    # 1) бизнес логика статуса заказа, метрик, уведомления клиента
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
        try:
            await query.answer("Ошибка обработки заказа", show_alert=True)
        except Exception:
            pass
        return

    # 2) визуальная пометка (по возможности) и дальнейший флоу
    suffix = "Принят в работу ✅" if decision == "approved" else "Отклонен ❌"

    # пытаемся обновить исходное сообщение
    try:
        base_text = query.message.text or ""
        await query.edit_message_text(
            text=base_text + f"\n\n<b>{suffix}</b>",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"Staff message updated for order {order_id}")
    except Exception:
        log.exception("Failed to edit staff message")

    # если отклонили, просто удаляем исходное сообщение и выходим
    if decision != "approved":
        try:
            await query.message.delete()
            log.info(f"Staff message deleted for order {order_id} (rejected)")
        except Exception:
            log.exception("Failed to delete staff message (rejected)")
        return

    # 3) approved: precreate courier (без ETA) + показать кнопки ETA стафу
    # важно: кнопки ETA должны прийти стафу даже если precreate упал

    target_idx = None
    order_row = None

    try:
        service = get_sheets_service()
        sheet = service.spreadsheets()

        rows = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="orders!A:Z",
        ).execute().get("values", [])

        for i, r in enumerate(rows[1:], start=2):
            if r and r[0] == order_id:
                target_idx = i
                order_row = r
                break

        if not target_idx or not order_row:
            log.error(
                f"order {order_id} not found after approve "
                f"(cannot precreate courier)"
            )
        else:
            try:
                payload = build_courier_payload(order_row)

                # precreate: ETA не задан, фиксируем это явно
                payload["pickup_eta_at"] = ""

                base_comment = order_row[7] if len(order_row) > 7 else ""
                payload["comment"] = (base_comment + "\nETA: pending").strip()

                res = await courier_create_order(payload)

                external_id = ""
                if isinstance(res, dict):
                    external_id = res.get("external_id", "") or ""

                # пишем внешний id и промежуточный статус ожидания ETA
                if external_id:
                    sheet.values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=f"orders!W{target_idx}",
                        valueInputOption="RAW",
                        body={"values": [[external_id]]},
                    ).execute()

                sheet.values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"orders!T{target_idx}",
                    valueInputOption="RAW",
                    body={"values": [["courier_wait_eta"]]},
                ).execute()

                log.info(
                    "courier precreate OK | "
                    f"order_id={order_id} target_idx={target_idx} "
                    f"external_id={external_id!r}"
                )

            except Exception as e:
                log.exception(
                    f"courier precreate failed for order {order_id}: {e}"
                )

    except Exception as e:
        log.exception(
            f"failed to load order row for courier precreate: {e}"
        )

    # 4) кнопки ETA стафу (всегда)
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Через сколько должен приехать курьер?",
            reply_markup=kb_staff_pickup_eta(order_id),
        )
    except Exception:
        log.exception(
            f"failed to send ETA buttons to staff for order {order_id}"
        )

    # 5) удаляем исходное сообщение стафа после того как кнопки уже отправлены
    try:
        await query.message.delete()
        log.info(f"Staff message deleted for order {order_id} (approved)")
    except Exception:
        log.exception("Failed to delete staff message (approved)")
