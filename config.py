# config.py
import os

# ====== TELEGRAM ======
BOT_TOKEN = os.getenv("BOT_TOKEN")

# админы ПЛАТФОРМЫ, не кухонь
ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set")

# ====== SPREADSHEETS ======
# platform sheet: users, registry, platform stats, etc
PLATFORM_SPREADSHEET_ID = os.getenv("PLATFORM_SPREADSHEET_ID") or os.getenv("SPREADSHEET_ID")

if not PLATFORM_SPREADSHEET_ID:
    raise RuntimeError("PLATFORM_SPREADSHEET_ID is not set")

# legacy alias (важно для старого кода, где миллиард упоминаний)
SPREADSHEET_ID = PLATFORM_SPREADSHEET_ID

# ranges
ORDERS_RANGE = "orders!A:AF"

# ====== WEB API ======
WEB_API_BASE_URL = "https://web-api-integration-production.up.railway.app"
WEB_API_KEY = "DEV_KEY"
WEB_API_TIMEOUT = 5
