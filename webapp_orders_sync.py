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

    rows = (
        sheet.values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=ORDERS_RANGE,
        )
        .execute()
        .get("values", [])
    )

    if len(rows) < 2:
        return

    for idx, row in enumerate(rows[1:], start=2):
        order_id = row[0] if len(row) > 0 else ""
        status = row[9] if len(row) > 9 else ""
        source = row[15] if len(row) > 15 else ""
        staff_notified = row[16] if len(row) > 16 else ""

        if not order_id:
            continue

        if status != "created" or source not in ("kitchen", "webapp") or staff_notified:
            log.info(
                f"SKIP order={order_id} status={status} source={source} staff_notified={staff_notified}"
            )
            continue

        log.info(f"📦 WEBAPP ORDER DETECTED {order_id} row={idx}")

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