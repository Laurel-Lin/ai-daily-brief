from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from fetch_sources import load_keywords
from utils import (
    DIGESTS_DIR,
    FEEDBACK_FILE,
    SELECTED_HISTORY_FILE,
    candidate_history_keys,
    canonical_title,
    canonical_url,
    is_in_report_window,
    normalize_text,
    read_json,
    read_yaml,
    utc_age_hours,
)

LOGGER = logging.getLogger("ai_daily_brief")

SOURCE_QUALITY = {
    "official": 95,
    "whitelist_social": 85,
    "x_official": 92,
    "x_researcher": 85,
    "x_builder": 82,
    "x_product_observer": 78,
    "x_chinese_observer": 78,
    "hn": 75,
    "reddit": 75,
    "github": 78,
    "huggingface": 80,
    "modelscope": 80,
    "chinese_media": 72,
    "chinese_community": 78,
    "technical_blog": 70,
    "media": 50,
}


def is_blocked(candidate: dict[str, Any], keywords: dict[str, list[str]]) -> tuple[bool, str]:
    text = normalize_text(f"{candidate.get('title', '')} {candidate.get('summary', '')}").lower()
    blocked_terms = [term.lower() for term in keywords.get("blocked", [])]
    if any(term in text for term in blocked_terms):
        return True, "命中过滤关键词"
    if re.search(r"\b(course|coupon|giveaway|sponsored|webinar)\b", text):
        return True, "疑似课程、广告或推广"
    if len(candidate.get("title", "")) < 5:
        return True, "标题信息不足"
    return False, ""


def source_quality(candidate: dict[str, Any]) -> int:
    return SOURCE_QUALITY.get(candidate.get("source_type"), 55)


def feedback_result(candidate: dict[str, Any]) -> tuple[int, list[str], bool]:
    config = read_yaml(FEEDBACK_FILE, {"rules": []})
    adjustment = 0
    notes: list[str] = []
    rejected = False
    searchable = {
        "title": normalize_text(candidate.get("title", "")).lower(),
        "url": canonical_url(candidate.get("url", "")).lower(),
        "source": normalize_text(candidate.get("source", "")).lower(),
        "source_type": normalize_text(candidate.get("source_type", "")).lower(),
        "tags": " ".join(candidate.get("tags", [])).lower(),
        "all": normalize_text(
            f"{candidate.get('title', '')} {candidate.get('summary', '')} {candidate.get('source', '')} "
            f"{' '.join(candidate.get('tags', []))}"
        ).lower(),
    }
    defaults = {"useful": 6, "neutral": 0, "avoid": -25}
    for rule in config.get("rules", []):
        if not isinstance(rule, dict) or not rule.get("match"):
            continue
        field = rule.get("field", "all")
        haystack = searchable.get(field, searchable["all"])
        if str(rule["match"]).lower() not in haystack:
            continue
        rating = str(rule.get("rating", "neutral")).lower()
        adjustment += int(rule.get("adjustment", defaults.get(rating, 0)))
        if rule.get("note"):
            notes.append(normalize_text(rule["note"]))
        if rating == "avoid":
            rejected = True
    return max(-30, min(15, adjustment)), notes, rejected


def bounded_log_score(value: int | float | None, base: int, cap: int = 100) -> float:
    if value in (None, "unknown"):
        return 25
    try:
        value = max(0, float(value))
    except (TypeError, ValueError):
        return 25
    if value <= 0:
        return 20
    return min(cap, math.log10(value + 1) / math.log10(base + 1) * cap)


def popularity_score(candidate: dict[str, Any]) -> float:
    metrics = candidate.get("metrics", {})
    source_type = candidate.get("source_type")
    if source_type == "github":
        star_delta_24h = metrics.get("star_delta_24h")
        star_delta_7d = metrics.get("star_delta_7d")
        fork_delta_7d = metrics.get("fork_delta_7d")
        has_delta = isinstance(star_delta_24h, int) or isinstance(star_delta_7d, int)
        delta_value = 0
        if isinstance(star_delta_24h, int):
            delta_value += star_delta_24h * 2
        if isinstance(star_delta_7d, int):
            delta_value += star_delta_7d
        star_delta_score = bounded_log_score(delta_value if has_delta else None, 300)
        total_star_score = bounded_log_score(metrics.get("stars"), 10000)
        fork_delta_score = bounded_log_score(fork_delta_7d if isinstance(fork_delta_7d, int) else None, 80)
        issue_activity_score = bounded_log_score(metrics.get("open_issues"), 300)
        signal_type = metrics.get("signal_type")
        recency_signal_score = {
            "new_project": 90,
            "fast_growing": 95,
            "major_release": 90,
            "mature_reference": 55,
            "maintenance_update": 10,
        }.get(signal_type, 35)
        score = (
            star_delta_score * 0.45
            + total_star_score * 0.20
            + fork_delta_score * 0.15
            + issue_activity_score * 0.10
            + recency_signal_score * 0.10
        )
        if not has_delta:
            score = min(65, total_star_score * 0.65 + recency_signal_score * 0.35)
        return score
    if source_type == "hn":
        points = bounded_log_score(metrics.get("points"), 800)
        comments = bounded_log_score(metrics.get("comments"), 400)
        return points * 0.6 + comments * 0.4
    if source_type == "reddit":
        upvotes = bounded_log_score(metrics.get("upvotes"), 2000)
        comments = bounded_log_score(metrics.get("comments"), 500)
        return upvotes * 0.55 + comments * 0.45
    if source_type in {"huggingface", "modelscope"}:
        likes = bounded_log_score(metrics.get("likes"), 2000)
        downloads = bounded_log_score(metrics.get("downloads"), 500000)
        discussion = bounded_log_score(metrics.get("discussion"), 200)
        return likes * 0.35 + downloads * 0.45 + discussion * 0.20
    if str(source_type).startswith("x_"):
        return bounded_log_score(metrics.get("x_heat"), 1000)
    if source_type in {"chinese_media", "chinese_community"}:
        if source_type == "chinese_community":
            heat = (metrics.get("likes") or 0) + (metrics.get("collects") or 0) * 2 + (metrics.get("comments") or 0) * 3
            return bounded_log_score(heat, 1000)
        return 60
    if source_type == "official":
        return 72
    return 50


def novelty_score(candidate: dict[str, Any]) -> float:
    age = utc_age_hours(candidate.get("published_at"))
    if age is None:
        return 55
    if age <= 24:
        return 95
    if age <= 72:
        return 84
    if age <= 168:
        return 68
    if age <= 720:
        return 45
    return 25


def product_inspiration_score(candidate: dict[str, Any]) -> float:
    text = normalize_text(
        f"{candidate.get('title', '')} {candidate.get('summary', '')} {' '.join(candidate.get('tags', []))}"
    ).lower()
    high_value_terms = [
        "agent",
        "ai coding",
        "coding agent",
        "mcp",
        "rag",
        "workflow",
        "tool use",
        "multimodal",
        "local llm",
        "open model",
        "cursor",
        "claude code",
        "devin",
        "qwen",
        "deepseek",
        "llama",
        "智能体",
        "编程",
        "多模态",
        "本地模型",
        "用户需求",
        "产品机会",
        "吐槽",
        "争议",
    ]
    hits = sum(1 for term in high_value_terms if term in text)
    score = 45 + min(45, hits * 12)
    if candidate.get("source_type") in {"github", "huggingface"}:
        score += 8
    return min(100, score)


def readable_value_score(candidate: dict[str, Any]) -> float:
    text = normalize_text(
        f"{candidate.get('title', '')} {candidate.get('summary', '')} {' '.join(candidate.get('tags', []))}"
    ).lower()
    source_type = candidate.get("source_type")
    score = 0

    if len(candidate.get("summary", "")) >= 35 or source_type in {"official", "github", "huggingface", "modelscope"}:
        score += 30
    elif len(candidate.get("title", "")) >= 12:
        score += 18

    problem_terms = [
        "build",
        "workflow",
        "agent",
        "rag",
        "search",
        "deploy",
        "debug",
        "test",
        "coding",
        "cli",
        "mcp",
        "orchestration",
        "retrieval",
        "multimodal",
        "inference",
        "memory",
        "tool",
        "自动化",
        "搜索",
        "部署",
        "调试",
        "编程",
        "检索",
        "工作流",
    ]
    if any(term in text for term in problem_terms):
        score += 25
    elif source_type == "official":
        score += 18

    metrics = candidate.get("metrics", {})
    has_heat = any(value not in (None, "", 0) for value in metrics.values())
    if has_heat or source_type == "official":
        score += 25
    else:
        score += 12

    focus_terms = [
        "ai",
        "llm",
        "agent",
        "coding",
        "mcp",
        "rag",
        "model",
        "multimodal",
        "openai",
        "claude",
        "cursor",
        "智能体",
        "大模型",
        "模型",
        "多模态",
        "编程",
    ]
    if any(term in text for term in focus_terms):
        score += 20
    elif source_type in {"official", "github"}:
        score += 12

    return min(100, score)


def enrich_reasoning(candidate: dict[str, Any]) -> None:
    title = candidate.get("title", "这条内容")
    source_type = candidate.get("source_type")
    if source_type == "github":
        candidate["why_important"] = f"{title} 的仓库指标和更新时间显示，它正在被开发者实际使用或维护。"
        candidate["inspiration"] = "优先看它把复杂能力封装成什么入口：CLI、服务、评测框架还是应用平台。"
    elif source_type in {"huggingface", "modelscope"}:
        candidate["why_important"] = f"{title} 的下载、点赞或讨论数据可以帮助判断模型是否进入真实试用阶段。"
        candidate["inspiration"] = "关注它是否降低部署成本、补齐中文/多模态能力，或适合接入 Agent 工具链。"
    elif source_type in {"hn", "reddit"}:
        candidate["why_important"] = f"{title} 的讨论数据可以暴露开发者和用户对同一问题的分歧。"
        candidate["inspiration"] = "重点看评论里的使用痛点、反对意见和未被满足的场景，而不只是标题本身。"
    elif source_type == "official":
        candidate["why_important"] = f"{candidate.get('source')} 直接发布，说明相关能力已经进入官方产品或平台叙事。"
        candidate["inspiration"] = "看它把 AI 能力放进哪个环节：开发、部署、调试、协作、评测或最终用户体验。"
    else:
        candidate["why_important"] = f"{title} 与当前 AI 产品或开发者工作流有关，需要结合原文确认具体价值。"
        candidate["inspiration"] = "如果它没有明确用户场景、热度依据或产品变化，就不应进入重点精选。"


def score_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    sq = source_quality(candidate)
    pop = popularity_score(candidate)
    nov = novelty_score(candidate)
    prod = product_inspiration_score(candidate)
    readable = readable_value_score(candidate)
    score = sq * 0.35 + pop * 0.25 + nov * 0.20 + prod * 0.20
    if candidate.get("source_type") == "github":
        score = apply_github_caps(candidate, score)
    feedback_adjustment, feedback_notes, feedback_rejected = feedback_result(candidate)
    score = max(0, min(100, score + feedback_adjustment))
    candidate["score"] = round(score, 2)
    candidate["readable_value"] = round(readable, 2)
    candidate["score_breakdown"] = {
        "source_quality": round(sq, 2),
        "popularity": round(pop, 2),
        "novelty": round(nov, 2),
        "product_inspiration": round(prod, 2),
        "readable_value": round(readable, 2),
        "feedback_adjustment": feedback_adjustment,
    }
    candidate["feedback"] = {
        "adjustment": feedback_adjustment,
        "notes": feedback_notes,
        "rejected": feedback_rejected,
    }
    enrich_reasoning(candidate)
    return candidate


def recent_release(metrics: dict[str, Any]) -> bool:
    age = utc_age_hours(metrics.get("latest_release_at"))
    return age is not None and age <= 24 * 7


def apply_github_caps(candidate: dict[str, Any], score: float) -> float:
    metrics = candidate.get("metrics", {})
    repo_age_days = metrics.get("repo_age_days")
    signal_type = metrics.get("signal_type")
    star_delta_7d = metrics.get("star_delta_7d")
    has_discussion = bool(metrics.get("external_discussion"))
    if signal_type == "maintenance_update":
        return min(score, 50)
    if isinstance(repo_age_days, int) and repo_age_days > 730 and signal_type not in {"fast_growing", "major_release"}:
        return min(score, 60)
    if (
        isinstance(repo_age_days, int)
        and repo_age_days > 365
        and not (isinstance(star_delta_7d, int) and star_delta_7d >= 50)
        and not recent_release(metrics)
        and not has_discussion
    ):
        return min(score, 70)
    return score


def story_urls(candidate: dict[str, Any]) -> set[str]:
    raw = candidate.get("raw", {})
    return {
        url for url in (
            canonical_url(candidate.get("url")),
            canonical_url(raw.get("story_url")),
        ) if url
    }


def title_tokens(candidate: dict[str, Any]) -> set[str]:
    title = canonical_title(candidate.get("title", ""))
    generic = {
        "ai", "llm", "new", "launch", "launches", "released", "releases", "introducing",
        "open", "source", "show", "hn", "the", "with", "for", "and", "发布", "推出", "开源",
    }
    return {token for token in title.split() if len(token) > 1 and token not in generic}


def same_story(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if story_urls(left).intersection(story_urls(right)):
        return True
    left_tokens = title_tokens(left)
    right_tokens = title_tokens(right)
    shared = left_tokens.intersection(right_tokens)
    union = left_tokens.union(right_tokens)
    if len(shared) >= 3 and bool(union) and len(shared) / len(union) >= 0.5:
        return True
    left_title = canonical_title(left.get("title", ""))
    right_title = canonical_title(right.get("title", ""))
    return bool(left_title and right_title) and SequenceMatcher(None, left_title, right_title).ratio() >= 0.78


def discussion_payload(candidate: dict[str, Any]) -> dict[str, Any] | None:
    comments = candidate.get("raw", {}).get("top_comments") or []
    if candidate.get("source_type") not in {"hn", "reddit"} and not comments:
        return None
    return {
        "source": candidate.get("source"),
        "source_type": candidate.get("source_type"),
        "url": candidate.get("raw", {}).get("discussion_url") or candidate.get("url"),
        "metrics": candidate.get("metrics", {}),
        "comments": comments,
    }


def merge_story(primary: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    related = primary.setdefault("related_sources", [])
    existing_ids = {item.get("id") for item in related}
    if other.get("id") not in existing_ids:
        related.append({
            "id": other.get("id"),
            "title": other.get("title"),
            "source": other.get("source"),
            "source_type": other.get("source_type"),
            "url": other.get("url"),
            "score": other.get("score"),
        })
    discussions = primary.setdefault("community_discussions", [])
    payload = discussion_payload(other)
    if payload and payload.get("url") not in {item.get("url") for item in discussions}:
        discussions.append(payload)
    own_payload = discussion_payload(primary)
    if own_payload and own_payload.get("url") not in {item.get("url") for item in discussions}:
        discussions.append(own_payload)
    primary["score"] = max(primary.get("score", 0), other.get("score", 0))
    primary["readable_value"] = max(primary.get("readable_value", 0), other.get("readable_value", 0))
    return primary


def deduplicate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (source_quality(item), item.get("score", 0)), reverse=True):
        match = next((item for item in clusters if same_story(item, candidate)), None)
        if match is None:
            candidate.setdefault("related_sources", [])
            candidate.setdefault("community_discussions", [])
            own_payload = discussion_payload(candidate)
            if own_payload:
                candidate["community_discussions"].append(own_payload)
            clusters.append(candidate)
        else:
            merge_story(match, candidate)
    return clusters


def selected_history_lookback_days() -> int:
    try:
        return max(0, int(os.getenv("SELECTED_HISTORY_DAYS", "14")))
    except ValueError:
        return 14


def env_nonnegative_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def recent_selected_keys(report_date: str | None, lookback_days: int | None = None) -> set[str]:
    if not report_date:
        return set()
    days = selected_history_lookback_days() if lookback_days is None else lookback_days
    if days <= 0:
        return set()
    try:
        current_date = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        return set()

    history = read_json(SELECTED_HISTORY_FILE, {"items": []})
    seen: set[str] = set()
    for item in history.get("items", []):
        try:
            item_date = datetime.strptime(item.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (current_date - timedelta(days=days) <= item_date < current_date):
            continue
        for key in item.get("keys", []):
            if key:
                seen.add(key)
    for digest_path in DIGESTS_DIR.glob("*.md"):
        try:
            item_date = datetime.strptime(digest_path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (current_date - timedelta(days=days) <= item_date < current_date):
            continue
        try:
            text = digest_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r"^### P\d+\｜(.+)$", text, flags=re.MULTILINE):
            title = normalize_text(match.group(1))
            title_key = canonical_title(title)
            if title_key:
                seen.add(f"title:{title_key}")
                seen.add(f"github:title:{title_key}")
            if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", title):
                seen.add(f"github:{title.lower()}")
    return seen


def repeated_from_history(candidate: dict[str, Any], seen_keys: set[str]) -> bool:
    return bool(seen_keys.intersection(candidate_history_keys(candidate)))


def filter_and_score(
    candidates: list[dict[str, Any]], *, min_score: int, max_items: int, report_date: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keywords = load_keywords()
    seen_history_keys = recent_selected_keys(report_date)
    blocked = []
    scorable = []
    for candidate in candidates:
        if report_date and not is_in_report_window(candidate.get("published_at"), report_date):
            blocked.append({"title": candidate.get("title"), "reason": "不在日报日期窗口内"})
            continue
        is_bad, reason = is_blocked(candidate, keywords)
        if is_bad:
            blocked.append({"title": candidate.get("title"), "reason": reason})
            continue
        scored = score_candidate(candidate)
        if scored.get("feedback", {}).get("rejected"):
            blocked.append({"title": scored.get("title"), "reason": "人工反馈标记为不再推荐"})
            continue
        if scored.get("source_type") == "github" and scored.get("metrics", {}).get("signal_type") == "maintenance_update":
            blocked.append({"title": scored.get("title"), "reason": "老项目普通维护更新"})
            continue
        scorable.append(scored)

    unique = deduplicate(scorable)
    eligible = []
    for candidate in unique:
        if candidate.get("score", 0) < min_score:
            continue
        if candidate.get("readable_value", 0) < 70:
            continue
        if candidate.get("score_breakdown", {}).get("product_inspiration", 0) < 70:
            continue
        if candidate.get("source_type") == "github":
            signal_type = candidate.get("metrics", {}).get("signal_type")
            if signal_type == "maintenance_update":
                continue
        if repeated_from_history(candidate, seen_history_keys):
            blocked.append({"title": candidate.get("title"), "reason": "最近已推送过"})
            continue
        eligible.append(candidate)

    eligible.sort(key=lambda item: item.get("score", 0), reverse=True)
    github_limit = env_nonnegative_int("GITHUB_MAX_SELECTED", 3)
    mature_limit = env_nonnegative_int("GITHUB_MATURE_MAX_SELECTED", 1)
    github_selected = 0
    mature_selected = 0
    selected = []
    for candidate in eligible:
        if candidate.get("source_type") == "github":
            if github_selected >= github_limit:
                blocked.append({"title": candidate.get("title"), "reason": "GitHub 当日精选上限"})
                continue
            if candidate.get("metrics", {}).get("signal_type") == "mature_reference":
                if mature_selected >= mature_limit:
                    blocked.append({"title": candidate.get("title"), "reason": "成熟 GitHub 项目当日上限"})
                    continue
                mature_selected += 1
            github_selected += 1
        selected.append(candidate)
        if len(selected) >= max_items:
            break
    selected = selected[:max_items]

    stats = {
        "raw_candidates": len(candidates),
        "scored_candidates": len(scorable),
        "deduplicated_candidates": len(unique),
        "selected_candidates": len(selected),
        "filtered_candidates": len(blocked) + max(0, len(unique) - len(selected)),
        "blocked_examples": blocked[:10],
        "main_filter_reasons": [
            "score < MIN_SCORE",
            "重复报道合并",
            "人工反馈标记为不再推荐",
            "营销、课程、广告或标题党内容过滤",
            "不在日报日期窗口内",
            "readable_value < 70",
            "产品启发分不足",
            "最近已推送过",
            "GitHub 当日精选上限",
            "老项目普通维护更新",
            "无法解释明确产品或技术启发",
        ],
    }
    LOGGER.info(
        "Filter stats: raw=%s scored=%s unique=%s selected=%s",
        stats["raw_candidates"],
        stats["scored_candidates"],
        stats["deduplicated_candidates"],
        stats["selected_candidates"],
    )
    return selected, stats
