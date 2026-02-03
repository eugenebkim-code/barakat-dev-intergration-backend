# webapp_orders_sync.py - ФИНАЛЬНАЯ ВЕРСИЯ

from datetime import datetime
import logging

from config import ORDERS_RANGE
from sheets_repo import get_sheets_service
from kitchen_context import require

log = logging.getLogger("WEBAPP_SYNC")

# Извлекаем имя листа из ORDERS_RANGE
ORDERS_SHEET = ORDERS_RANGE.split("!")[0]


async def orders_job(context):
    """
    ОБЪЕДИНЕННАЯ job: sync + notify.
    
    Логика:
    1. Читает Google Sheets
    2. Для каждой строки:
       - Если AE пусто → пишет "1"
       - Если AE="1" и AF пусто → вызывает notify_staff() и пишет AF
    """
    job = context.job
    data = job.data

    spreadsheet_id = data["spreadsheet_id"]
    kitchen_id = data["kitchen_id"]

    try:
        kitchen = require(kitchen_id)
    except Exception as e:
        log.error(f"[{kitchen_id}] Kitchen not found: {e}")
        return

    bot = context.bot
    service = get_sheets_service()

    try:
        rows = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=ORDERS_RANGE,
        ).execute().get("values", [])
    except Exception as e:
        log.error(f"[{kitchen_id}] Failed to read sheets: {e}")
        return

    if len(rows) < 2:
        return

    sync_updates = []
    notify_updates = []

    for idx, row in enumerate(rows[1:], start=2):
        order_id = row[0] if row else f"row_{idx}"
        
        # Колонка AE = index 30
        ae = row[30] if len(row) > 30 else ""
        
        # Колонка AF = index 31
        af = row[31] if len(row) > 31 else ""

        # ===== SYNC: Если AE пусто → записываем "1" =====
        if not ae:
            sync_updates.append({
                "range": f"{ORDERS_SHEET}!AE{idx}",
                "values": [["1"]],
            })
            log.info(f"[{kitchen_id}] SYNC: order={order_id} row={idx} -> AE=1")
            continue  # пропускаем notify для этой строки (AE только что записали)

        # ===== NOTIFY: Если AE="1" и AF пусто → notify =====
        if ae == "1" and not af:
            log.info(f"[{kitchen_id}] NOTIFY: order={order_id} row={idx}")

            try:
                from main import notify_staff

                await notify_staff(bot, kitchen, order_id)

                notify_updates.append({
                    "range": f"{ORDERS_SHEET}!AF{idx}",
                    "values": [[f"notified:{datetime.utcnow().isoformat()}"]],
                })
                
                log.info(f"[{kitchen_id}] NOTIFY: order={order_id} -> AF=notified")

            except ImportError as e:
                log.error(f"[{kitchen_id}] Failed to import notify_staff: {e}")
                
            except Exception as e:
                log.error(f"[{kitchen_id}] notify_staff failed for {order_id}: {e}", exc_info=True)

    # ===== Батч-апдейты =====
    all_updates = sync_updates + notify_updates

    if all_updates:
        try:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "valueInputOption": "RAW",
                    "data": all_updates,
                },
            ).execute()
            
            if sync_updates:
                log.info(f"[{kitchen_id}] Wrote {len(sync_updates)} AE updates")
            if notify_updates:
                log.info(f"[{kitchen_id}] Wrote {len(notify_updates)} AF updates")
                
        except Exception as e:
            log.error(f"[{kitchen_id}] Failed to write updates: {e}")