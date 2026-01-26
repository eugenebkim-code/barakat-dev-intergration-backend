# sheets_repo.py

import os
import json
import base64
from typing import Tuple, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# -------------------------------------------------
# ENV
# -------------------------------------------------

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")

if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is not set")

if not GOOGLE_SERVICE_ACCOUNT_JSON_B64:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is not set")


# -------------------------------------------------
# Sheets service
# -------------------------------------------------

def get_sheets_service():
    service_account_info = json.loads(
        base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
    )

    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    return build("sheets", "v4", credentials=creds)


# -------------------------------------------------
# Orders
# -------------------------------------------------

def find_order_row_by_id(order_id: str) -> Tuple[Optional[int], Optional[dict]]:
    service = get_sheets_service()
    sheet = service.spreadsheets()

    rows = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="orders!A:Q",
    ).execute().get("values", [])

    for idx, row in enumerate(rows[1:], start=2):
        if row and row[0] == order_id:
            return idx, {
                "order_id": row[0],
                "created_at": row[1],
                "user_id": row[2],
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
