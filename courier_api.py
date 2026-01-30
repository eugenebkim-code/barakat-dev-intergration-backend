# courier_api.py

import os
import logging
import httpx

log = logging.getLogger("COURIER_API")

COURIER_API_URL = os.getenv("COURIER_API_URL", "")
API_KEY = os.getenv("API_KEY", "")
COURIER_API_TIMEOUT = 10


async def courier_create_order(payload: dict) -> dict:
    log.error("### REAL courier_create_order CALLED ###")
    """
    Создает заказ курьеру.
    Никогда не кидает исключения наружу.
    """

    if not COURIER_API_URL:
        log.warning("COURIER_API_URL not set, using stub courier_create_order")
        return {
            "status": "ok",
            "mode": "stub",
            "external_id": f"STUB-{payload.get('order_id')}",
        }

    try:
        async with httpx.AsyncClient(timeout=COURIER_API_TIMEOUT) as client:
            resp = await client.post(
                f"{COURIER_API_URL}/api/v1/orders",
                json=payload,
                headers={
                    "X-API-KEY": API_KEY,
                },
            )

        if resp.status_code != 200:
            log.error(
                f"COURIER create_order failed "
                f"status={resp.status_code} body={resp.text}"
            )
            return {"status": "error", "reason": "http_error"}

        return resp.json()

    except Exception as e:
        log.exception(f"COURIER create_order exception: {e}")
        return {"status": "error", "reason": "exception"}
