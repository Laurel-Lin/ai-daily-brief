from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = PROJECT_ROOT / "sources"
DIGESTS_DIR = PROJECT_ROOT / "digests"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOGS_DIR / "app.log"


def load_environment() -> None:
    if load_dotenv:
        load_dotenv(PROJECT_ROOT / ".env")


def ensure_dirs() -> None:
    for path in (DIGESTS_DIR, DATA_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return logging.getLogger("ai_daily_brief")


def get_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))


def today_string() -> str:
    requested_date = os.getenv("BRIEF_DATE")
    if requested_date:
        try:
            datetime.strptime(requested_date, "%Y-%m-%d")
            return requested_date
        except ValueError:
            logging.getLogger("ai_daily_brief").warning("Invalid BRIEF_DATE=%s, fallback to today", requested_date)
    return datetime.now(get_timezone()).strftime("%Y-%m-%d")


def yesterday_string() -> str:
    return (datetime.now(get_timezone()) - timedelta(days=1)).strftime("%Y-%m-%d")


def report_date_window(date_str: str) -> tuple[datetime, datetime]:
    tz = get_timezone()
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def is_in_report_window(published_at: str | None, date_str: str) -> bool:
    if not published_at:
        return True
    try:
        normalized = published_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        start, end = report_date_window(date_str)
        local_dt = dt.astimezone(get_timezone())
        return start <= local_dt < end
    except ValueError:
        return True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def read_yaml(path: Path, fallback: Any) -> Any:
    try:
        import yaml
    except Exception:
        logging.getLogger("ai_daily_brief").warning("PyYAML unavailable, using fallback for %s", path)
        return fallback

    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_title(title: str) -> str:
    title = normalize_text(title).lower()
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", title)
    stopwords = {"the", "a", "an", "and", "or", "to", "for", "with", "by", "on", "of"}
    words = [word for word in title.split() if word not in stopwords]
    return " ".join(words[:16])


def stable_id(*parts: str) -> str:
    raw = "|".join(part or "" for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def utc_age_hours(published_at: str | None) -> float | None:
    if not published_at:
        return None
    try:
        normalized = published_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600)
    except ValueError:
        return None


def truncate(text: str, length: int) -> str:
    text = normalize_text(text)
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)].rstrip() + "…"


def format_metric(value: Any) -> str:
    if value is None:
        return "未知"
    return str(value)


def request_json(url: str, *, timeout: int = 15, headers: dict[str, str] | None = None) -> Any:
    try:
        import requests

        response = requests.get(url, timeout=timeout, headers=headers or {})
        response.raise_for_status()
        return response.json()
    except Exception:
        logging.getLogger("ai_daily_brief").exception("Request failed: %s", url)
        raise


def request_text(url: str, *, timeout: int = 15, headers: dict[str, str] | None = None) -> str:
    try:
        import requests

        response = requests.get(url, timeout=timeout, headers=headers or {})
        response.raise_for_status()
        return response.text
    except Exception:
        logging.getLogger("ai_daily_brief").exception("Request failed: %s", url)
        raise
