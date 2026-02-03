# webapi_client.py

import httpx
import os
import logging

log = logging.getLogger(__name__)

WEB_API_URL = os.getenv("WEB_API_URL", "")
API_KEY = os.getenv("WEB_API_KEY", "")
WEB_API_TIMEOUT = 5


async def webapi_create_order(payload: dict) -> dict:
    """
    Safe Web API order creation.
    Никогда не бросает исключения наружу.
    """

    log.info(
        "[CREATE_ORDER] from=%s order_id=%s client=%s",
        payload.get("source"),
        payload.get("order_id"),
        payload.get("client_tg_id"),
    )

    # DEV / fallback режим
    if not WEB_API_URL:
        log.warning("WEB_API_URL not set, using stub create_order")
        return {
            "status": "ok",
            "mode": "stub",
            "order_id": payload.get("order_id"),
        }

    try:
        async with httpx.AsyncClient(timeout=WEB_API_TIMEOUT) as client:
            resp = await client.post(
                f"{WEB_API_URL}/api/v1/orders",
                json=payload,
                headers={
                    "X-API-KEY": API_KEY,
                    "X-ROLE": "kitchen",
                },
            )

        if resp.status_code != 200:
            log.error(
                "WEBAPI create_order failed | status=%s body=%s",
                resp.status_code,
                resp.text,
            )
            return {"status": "error", "reason": "http_error"}

        return resp.json()

    except Exception as e:
        log.exception("WEBAPI create_order exception")
        return {"status": "error", "reason": "exception"}


async def webapi_check_address(payload: dict) -> dict:
    # ... existing code до result = resp.json() ...
    
    result = resp.json()
    
    # ✅ ВАЛИДАЦИЯ КОНТРАКТА API
    if result.get("ok") is True:
        if "delivery_price" not in result:
            log.error(
                "[WEBAPI CONTRACT VIOLATION] "
                "Web API returned ok=true but NO delivery_price! "
                f"payload={payload} response={result}"
            )
            return {
                "ok": False,
                "message": "API contract violation: missing delivery_price"
            }
        
        # Проверка корректности значения
        try:
            price = float(result["delivery_price"])
            if price < 0:
                log.error(
                    "[WEBAPI CONTRACT VIOLATION] "
                    f"Negative delivery_price={price}!"
                )
                return {
                    "ok": False,
                    "message": "API contract violation: negative price"
                }
        except (TypeError, ValueError):
            log.error(
                "[WEBAPI CONTRACT VIOLATION] "
                f"Invalid delivery_price format: {result.get('delivery_price')!r}"
            )
            return {
                "ok": False,
                "message": "API contract violation: invalid price format"
            }
    
    return result