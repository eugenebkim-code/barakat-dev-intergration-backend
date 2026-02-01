# sheets_repo.py

import os
import json
from pathlib import Path
from typing import Tuple, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# -------------------------------------------------
# ENV
# -------------------------------------------------

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is not set")


# -------------------------------------------------
# CONSTANTS
# -------------------------------------------------

ORDERS_RANGE = "orders!A:AD"


# -------------------------------------------------
# Service account file
# -------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
SERVICE_ACCOUNT_PATH = BASE_DIR / "service_account.json"

if not SERVICE_ACCOUNT_PATH.exists():
    raise RuntimeError(f"service_account.json not found at {SERVICE_ACCOUNT_PATH}")


# -------------------------------------------------
# Sheets service
# -------------------------------------------------

def get_sheets_service():
    with open(SERVICE_ACCOUNT_PATH, "r", encoding="utf-8") as f:
        service_account_info = json.load(f)

    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    return build("sheets", "v4", credentials=creds)


# -------------------------------------------------
# Orders
# -------------------------------------------------

def find_order_row_by_id(
    order_id: str,
    *,
    spreadsheet_id: Optional[str] = None,
) -> Tuple[Optional[int], Optional[dict]]:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    sid = spreadsheet_id or SPREADSHEET_ID

    rows = sheet.values().get(
        spreadsheetId=sid,
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


def update_order_cells(row_idx: int, updates: dict):
    service = get_sheets_service()
    sheet = service.spreadsheets()

    col_map = {
        "status": "J",
        "handled_at": "K",
        "handled_by": "L",
        "reaction_seconds": "M",
    }

    for key, value in updates.items():
        if key not in col_map:
            continue

        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"orders!{col_map[key]}{row_idx}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()
