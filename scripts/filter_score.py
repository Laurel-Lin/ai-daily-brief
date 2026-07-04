from __future__ import annotations

import logging
import math
import re
from typing import Any

from fetch_sources import load_keywords
from utils import canonical_title, is_in_report_window, normalize_text, utc_age_hours

LOGGER = logging.getLogger("ai_daily_brief")

SOURCE_QUALITY = {
    "official": 95,
    "whitelist_social": 85,
    "hn": 75,
    "reddit": 75,
    "github": 78,
    "huggingface": 80,
    "modelscope": 80,
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


def bounded_log_score(value: int | float | None, base: int, cap: int = 100) -> float:
    if value is None:
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
        stars = bounded_log_score(metrics.get("stars"), 5000)
        forks = bounded_log_score(metrics.get("forks"), 1000)
        issues = bounded_log_score(metrics.get("open_issues"), 300)
        return stars * 0.55 + forks * 0.25 + issues * 0.20
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
    candidate["score"] = round(score, 2)
    candidate["readable_value"] = round(readable, 2)
    candidate["score_breakdown"] = {
        "source_quality": round(sq, 2),
        "popularity": round(pop, 2),
        "novelty": round(nov, 2),
        "product_inspiration": round(prod, 2),
        "readable_value": round(readable, 2),
    }
    enrich_reasoning(candidate)
    return candidate


def deduplicate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate.get("url") or canonical_title(candidate.get("title", ""))
        title_key = canonical_title(candidate.get("title", ""))
        dedupe_key = key.lower().strip() or title_key
        existing = by_key.get(dedupe_key) or by_key.get(title_key)
        if not existing:
            by_key[dedupe_key] = candidate
            if title_key:
                by_key[title_key] = candidate
            continue
        existing_quality = source_quality(existing)
        candidate_quality = source_quality(candidate)
        if candidate_quality > existing_quality or candidate.get("score", 0) > existing.get("score", 0):
            by_key[dedupe_key] = candidate
            if title_key:
                by_key[title_key] = candidate
    seen_ids = set()
    unique = []
    for candidate in by_key.values():
        candidate_id = candidate.get("id")
        if candidate_id not in seen_ids:
            seen_ids.add(candidate_id)
            unique.append(candidate)
    return unique


def filter_and_score(
    candidates: list[dict[str, Any]], *, min_score: int, max_items: int, report_date: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keywords = load_keywords()
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
        scorable.append(score_candidate(candidate))

    unique = deduplicate(scorable)
    selected = [
        candidate
        for candidate in unique
        if candidate.get("score", 0) >= min_score
        and candidate.get("readable_value", 0) >= 70
        and candidate.get("score_breakdown", {}).get("product_inspiration", 0) >= 70
    ]
    selected.sort(key=lambda item: item.get("score", 0), reverse=True)
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
            "营销、课程、广告或标题党内容过滤",
            "不在日报日期窗口内",
            "readable_value < 70",
            "产品启发分不足",
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
