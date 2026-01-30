#webapp_orders_sync.py

import sys
from pathlib import Path
from datetime import datetime


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
        range="orders!A:Q",
    ).execute().get("values", [])

    if len(rows) < 2:
        return

    for idx, row in enumerate(rows[1:], start=2):
        status = row[9] if len(row) > 9 else ""
        staff_notified = row[16] if len(row) > 16 else ""

        if status != "pending" or staff_notified:
            log.info(
                f"CHECK order={row[0]} status={status} staff_notified={staff_notified}"
            )
            continue

        order_id = row[0]
        log.info(f"📦 WEBAPP ORDER DETECTED {order_id}")
        # WebApp уже уведомляет стаф напрямую.
        # Здесь мы только фиксируем, что заказ замечен job'ом.
        log.info(f"✅ WEBAPP order {order_id} marked as seen by sync job")
            