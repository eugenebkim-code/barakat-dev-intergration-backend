#utils_spreadsheet.py

from config import SPREADSHEET_ID

def get_active_spreadsheet_id(context):
    """
    Возвращает spreadsheet_id активной кухни.
    Fallback — bootstrap кухня (env SPREADSHEET_ID).
    """
    return context.user_data.get("spreadsheet_id") or SPREADSHEET_ID