# sheets_repo.py

import os
import json
import base64
from typing import Tuple, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# -------------------------------------------------
# CONSTANTS
# -------------------------------------------------

ORDERS_RANGE = "orders!A:AD"
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# -------------------------------------------------
# Sheets service (lazy, b64, Railway-safe)
# -------------------------------------------------

_sheets_service = None


def get_sheets_service():
    global _sheets_service
    if _sheets_service:
        return _sheets_service

    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "").strip()
    if not b64:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 is not set")

    try:
        service_account_info = json.loads(
            base64.b64decode(b64).decode("utf-8")
        )
    except Exception as e:
        raise RuntimeError("Invalid GOOGLE_SERVICE_ACCOUNT_B64") from e

    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=_SCOPES,
    )

    _sheets_service = build(
        "sheets",
        "v4",
        credentials=creds,
        cache_discovery=False,
    )

    return _sheets_service


# -------------------------------------------------
# Orders
# -------------------------------------------------

def find_order_row_by_id(
    order_id: str,
    *,
    spreadsheet_id: str,
) -> Tuple[Optional[int], Optional[dict]]:
    """
    Ищет заказ по order_id.
    spreadsheet_id ОБЯЗАТЕЛЕН.
    """
    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range=ORDERS_RANGE,
    ).execute().get("values", [])

    for idx, row in enumerate(rows[1:], start=2):
        if row and row[0] == order_id:
            return idx, {
                "order_id": row[0],
                "created_at": row[1] if len(row) > 1 else "",
                "user_id": row[2] if len(row) > 2 else "",
            }

    return None, None


def update_order_cells(
    *,
    row_idx: int,
    updates: dict,
    spreadsheet_id: str,
):
    """
    Обновляет ячейки заказа.

    row_idx: номер строки заказа (1-based)
    updates: словарь вида {column_name: value}
    spreadsheet_id: ОБЯЗАТЕЛЕН
    """
    service = get_sheets_service()
    sheet = service.spreadsheets()

    col_map = {
        "status": "J",
        "handled_at": "K",
        "handled_by": "L",
        "reaction_seconds": "M",
    }

    for key, value in updates.items():
        col = col_map.get(key)
        if not col:
            continue

        sheet.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"orders!{col}{row_idx}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()
