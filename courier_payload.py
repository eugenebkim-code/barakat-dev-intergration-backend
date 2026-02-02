#courier_payload.py

from datetime import datetime, timezone
import inspect
import logging

log = logging.getLogger(__name__)

def build_courier_payload(
    order_row: list,
    *,
    pickup_eta_at: str | None = None,
    eta_minutes: int | None = None,
    pickup_address_fallback: str = "충남 아산시 둔포면 둔포중앙로161번길 21-2",
    city_fallback: str = "dunpo",
) -> dict:
    """
    Формирует payload для курьерки ИМЕННО из строки kitchen orders.

    Ожидаемая схема kitchen orders (по индексам):
      0  A  order_id
      1  B  created_at
      2  C  user_id (client_tg_id)
      3  D  username
      4  E  items
      5  F  total_price
      6  G  type
      7  H  comment
      8  I  payment_proof (может быть file_id)
      9  J  status
      13 N  address (delivery_address)
      14 O  delivery_fee (price_krw)
      17 R  pickup_eta_at
    """

    log.error(
        f"[courier_payload] called from file={inspect.getsourcefile(build_courier_payload)} "
        f"pickup_eta_at(arg)={pickup_eta_at!r}"
    )

    order_id = order_row[0] if len(order_row) > 0 else ""

    # client
    client_tg_id = None
    try:
        if len(order_row) > 2 and str(order_row[2]).strip():
            client_tg_id = int(str(order_row[2]).strip())
    except Exception:
        client_tg_id = None

    client_username = order_row[3] if len(order_row) > 3 else ""
    items_text = order_row[4] if len(order_row) > 4 else ""

    # comment (именно колонка H)
    comment = order_row[7] if len(order_row) > 7 else ""

    # delivery address (именно колонка N)
    delivery_address = order_row[13] if len(order_row) > 13 else ""

    # delivery fee (колонка O)
    price_krw = 0
    try:
        if len(order_row) > 14 and str(order_row[14]).strip():
            price_krw = int(float(str(order_row[14]).strip()))
    except Exception:
        price_krw = 0

    # ETA: приоритет аргумента, затем sheet (колонка R)
    eta_from_sheet = order_row[17] if len(order_row) > 17 else ""
    eta = pickup_eta_at or eta_from_sheet

    log.error(
        f"[courier_payload] ETA resolved "
        f"pickup_eta_at(arg)={pickup_eta_at!r} "
        f"eta_from_sheet={eta_from_sheet!r}"
    )

    if not eta:
        eta = datetime.now(timezone.utc).isoformat()
        log.error(f"[courier_payload] ETA was empty -> forced utc now = {eta}")

    # доклеиваем пометку про ETA минутами в комментарий
    comment_parts = []
    if comment and str(comment).strip():
        comment_parts.append(str(comment).strip())
    if eta_minutes:
        comment_parts.append(f"Курьер нужен через {eta_minutes} мин")
    final_comment = " | ".join(comment_parts) if comment_parts else ""

    payload = {
        "order_id": order_id,
        "source": "kitchen",
        "client_tg_id": client_tg_id,
        "client_name": client_username or "",
        # у кухни телефона нет, кладем items чтобы курьер видел состав
        "client_phone": items_text or "",
        "pickup_address": pickup_address_fallback,
        "delivery_address": delivery_address or "",
        "pickup_eta_at": eta,
        "city": city_fallback,
        "comment": final_comment,
        "price_krw": price_krw,
    }

    log.error(f"[courier_payload] FINAL pickup_eta_at={payload['pickup_eta_at']!r}")
    log.error(f"[courier_payload] FINAL pickup_address={payload['pickup_address']!r} delivery_address={payload['delivery_address']!r}")

    return payload
