from datetime import datetime, timezone
import inspect
import logging

log = logging.getLogger(__name__)


def build_courier_payload(order_row: list, *, pickup_eta_at: str | None = None) -> dict:
    """
    Формирует payload под Courier Bridge API.
    pickup_eta_at передается ЯВНО, если есть (приоритетнее sheet).
    """

    log.error(
        f"[courier_payload] called from file={inspect.getsourcefile(build_courier_payload)} "
        f"pickup_eta_at(arg)={pickup_eta_at!r}"
    )

    order_id = order_row[0]

    client_tg_id = int(order_row[2]) if len(order_row) > 2 and order_row[2] else None
    client_name = order_row[3] if len(order_row) > 3 else ""
    client_phone = order_row[4] if len(order_row) > 4 else ""

    delivery_address = order_row[13] if len(order_row) > 13 else ""
    comment = order_row[7] if len(order_row) > 7 else ""

    # ETA: приоритет аргумента, затем sheet
    eta_from_sheet = order_row[17] if len(order_row) > 17 else ""
    eta = pickup_eta_at or eta_from_sheet

    log.error(
        f"[courier_payload] ETA resolved "
        f"pickup_eta_at(arg)={pickup_eta_at!r} "
        f"eta_from_sheet={eta_from_sheet!r}"
    )

    # ЖЕСТКАЯ защита от пустого значения (422)
    if not eta:
        eta = datetime.now(timezone.utc).isoformat()
        log.error(f"[courier_payload] ETA was empty -> forced utc now = {eta}")

    payload = {
        "order_id": order_id,
        "source": "flower_shop",
        "client_tg_id": client_tg_id,
        "client_name": client_name,
        "client_phone": client_phone,
        "pickup_address": "KITCHEN_ADDRESS",
        "delivery_address": delivery_address,
        "pickup_eta_at": eta,
        "city": "Asan",
        "comment": comment,
    }

    log.error(f"[courier_payload] FINAL pickup_eta_at={payload['pickup_eta_at']!r}")

    return payload