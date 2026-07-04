from __future__ import annotations

import logging
from pathlib import Path

from fetch_sources import fetch_all_sources
from filter_score import filter_and_score
from push_serverchan import push_serverchan
from summarize import render_markdown, render_wechat_summary
from utils import DATA_DIR, DIGESTS_DIR, env_int, load_environment, now_iso, setup_logging, today_string, write_json


def main() -> int:
    load_environment()
    logger = setup_logging()
    date_str = today_string()
    min_score = env_int("MIN_SCORE", 75)
    max_items = env_int("MAX_ITEMS", 8)

    logger.info("AI daily brief started at %s", now_iso())
    logger.info("Run date=%s min_score=%s max_items=%s", date_str, min_score, max_items)

    candidates = fetch_all_sources(report_date=date_str)
    write_json(DATA_DIR / "raw_candidates.json", {"generated_at": now_iso(), "items": candidates})

    selected, stats = filter_and_score(candidates, min_score=min_score, max_items=max_items, report_date=date_str)
    markdown = render_markdown(date_str, selected, stats)

    digest_path = DIGESTS_DIR / f"{date_str}.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(markdown, encoding="utf-8")
    logger.info("Markdown saved: %s", digest_path)

    artifact_name = f"ai-daily-brief-{date_str}"
    latest = {
        "generated_at": now_iso(),
        "date": date_str,
        "digest_path": str(Path("digests") / f"{date_str}.md"),
        "artifact_name": artifact_name,
        "stats": stats,
        "selected": selected,
    }
    write_json(DATA_DIR / "latest.json", latest)

    push_title, push_content = render_wechat_summary(date_str, selected)
    push_ok = push_serverchan(push_title, push_content)
    logger.info("Artifact name: %s", artifact_name)
    logger.info("Server酱 push success: %s", push_ok)
    logger.info("AI daily brief finished at %s", now_iso())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
