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
    """
    Проверка адреса через Web API.
    Safe: не бросает исключения наружу.
    """

    # DEV / fallback режим
    if not WEB_API_URL:
        log.warning("WEB_API_URL not set, using stub check_address")
        return {
            "ok": True,
            "normalized_address": payload.get("address", "").strip(),
            "zone": "STUB_ZONE",
            "message": "Адрес проверен (stub)",
        }

    try:
        async with httpx.AsyncClient(timeout=WEB_API_TIMEOUT) as client:
            resp = await client.post(
                f"{WEB_API_URL}/api/v1/address/check",
                json=payload,
                headers={
                    "X-API-KEY": API_KEY,
                    "X-ROLE": "kitchen",
                },
            )

        if resp.status_code != 200:
            log.error(
                "WEBAPI check_address failed | status=%s body=%s",
                resp.status_code,
                resp.text,
            )
            return {"ok": False, "message": "http_error"}

        return resp.json()

    except Exception as e:
        log.exception("WEBAPI check_address exception")
        return {"ok": False, "message": "exception"}