from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger("ai_daily_brief")


def push_serverchan(title: str, content: str) -> bool:
    sendkey = os.getenv("SERVERCHAN_SENDKEY")
    if not sendkey:
        LOGGER.info("SERVERCHAN_SENDKEY not set; skip Server酱 push")
        return False

    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    try:
        import requests

        response = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        if response.ok:
            payload = response.json()
            if payload.get("code") == 0:
                LOGGER.info("Server酱 push succeeded")
                return True
            LOGGER.error("Server酱 push failed: %s", payload)
            return False
        LOGGER.error("Server酱 push HTTP failed: status=%s body=%s", response.status_code, response.text[:300])
        return False
    except Exception as exc:
        LOGGER.error("Server酱 push raised an exception: %s", exc.__class__.__name__)
        return False
