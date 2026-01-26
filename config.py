# config.py
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # <-- ВАЖНО

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not ADMIN_CHAT_ID:
    raise RuntimeError("ADMIN_CHAT_ID is not set")

if not OWNER_CHAT_ID:
    raise RuntimeError("OWNER_CHAT_ID is not set")

if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is not set")


ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)
OWNER_CHAT_ID_INT = int(OWNER_CHAT_ID)

STAFF_CHAT_IDS = {
    int(x)
    for x in os.getenv("STAFF_CHAT_IDS", "").split(",")
    if x.strip().isdigit()
}
STAFF_CHAT_IDS.add(ADMIN_CHAT_ID_INT)
