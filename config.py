# config.py
import os


# ====== TELEGRAM (ПЛАТФОРМА) ======

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# админы ПЛАТФОРМЫ, не кухонь
ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set")


# ====== WEB API ======

WEB_API_BASE_URL = os.getenv(
    "WEB_API_BASE_URL",
    "https://web-api-integration-production.up.railway.app",
)

WEB_API_KEY = os.getenv("WEB_API_KEY", "")
WEB_API_TIMEOUT = int(os.getenv("WEB_API_TIMEOUT", "5"))


# ====== FEATURE FLAGS / DEFAULTS ======

DEFAULT_ORDERS_RANGE = "orders!A:AF"