#webapp_orders_sync.py

import sys
from pathlib import Path
from datetime import datetime
from config import (
    BOT_TOKEN,
    OWNER_CHAT_ID_INT,
    ADMIN_CHAT_ID_INT,
    STAFF_CHAT_IDS,
    SPREADSHEET_ID,
    ORDERS_RANGE,   # 👈 ВОТ ЭТО ДОБАВЛЯЕМ
)

WEB_API_PATH = Path(__file__).resolve().parents[1] / "raduga_demo_web_api"
sys.path.append(str(WEB_API_PATH))

import logging
from sheets_repo import get_sheets_service
from main import get_order_from_sheet

log = logging.getLogger("WEBAPP_SYNC")

async def webapp_orders_job(context):
    spreadsheet_id = context.job.data["spreadsheet_id"]

    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    if len(rows) < 2:
        return

    for idx, row in enumerate(rows[1:], start=2):
        order_id = row[0] if len(row) > 0 else ""
        status = row[9] if len(row) > 9 else ""
        staff_notified = row[16] if len(row) > 16 else ""

        # Мы ловим только новые webapp-заказы (status=created) и только если еще не помечены
        if status != "created" or staff_notified:
            # чтобы не спамить логами каждые 10 сек по всем строкам
            continue

        log.info(f"📦 WEBAPP ORDER DETECTED {order_id} row={idx}")

        # Помечаем, что заказ увиден job'ом, чтобы не обрабатывать его снова
        # (колонка Q = staff_message_id, индекс 16)
        try:
            sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=f"Q{idx}",
                valueInputOption="RAW",
                body={"values": [[f"seen:{datetime.utcnow().isoformat()}"]]},
            ).execute()

            log.info(f"✅ WEBAPP order {order_id} marked as seen (Q{idx})")

        except Exception:
            log.exception(f"❌ Failed to mark WEBAPP order {order_id} as seen")
            continue
            