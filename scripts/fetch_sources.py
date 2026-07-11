from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

from utils import DATA_DIR, SOURCES_DIR, days_between, normalize_text, read_json, read_yaml, request_json, request_text, stable_id, write_json

LOGGER = logging.getLogger("ai_daily_brief")
USER_AGENT = "ai-daily-brief/0.1 (+https://github.com/)"
UNKNOWN = "unknown"


def make_candidate(
    *,
    title: str,
    url: str,
    source: str,
    source_type: str,
    published_at: str | None = None,
    summary: str = "",
    metrics: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": stable_id(source_type, url, title),
        "title": normalize_text(title),
        "url": url,
        "source": source,
        "source_type": source_type,
        "published_at": published_at,
        "summary": normalize_text(summary),
        "metrics": metrics or {},
        "tags": tags or [],
        "raw": raw or {},
        "score": 0,
        "score_breakdown": {},
        "why_important": "",
        "inspiration": "",
    }


def load_keywords() -> dict[str, list[str]]:
    fallback = {
        "english": ["AI", "LLM", "agent", "AI coding", "MCP", "RAG", "multimodal", "OpenAI", "Anthropic"],
        "chinese": ["AI", "智能体", "大模型", "编程助手", "多模态", "开源模型"],
        "downrank": ["funding", "raised", "acquires"],
        "blocked": ["course", "coupon", "giveaway", "sponsored", "排行榜", "课程", "优惠"],
    }
    return read_yaml(SOURCES_DIR / "keywords.yml", fallback)


def keyword_match(text: str, keywords: dict[str, list[str]]) -> bool:
    text_lower = text.lower()
    terms = keywords.get("english", []) + keywords.get("chinese", [])
    return any(term.lower() in text_lower for term in terms)


def parse_rss_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def parse_rss_xml(text: str, source_name: str, source_type: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    items = []
    for item in root.findall(".//item")[:30]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        description = item.findtext("description") or ""
        published = parse_rss_datetime(item.findtext("pubDate"))
        if title and link:
            items.append(
                make_candidate(
                    title=title,
                    url=link,
                    source=source_name,
                    source_type=source_type,
                    published_at=published,
                    summary=description,
                    metrics={},
                    raw={"fetch_type": "rss"},
                )
            )
    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:30]:
        title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        link_node = entry.find("{http://www.w3.org/2005/Atom}link")
        link = link_node.attrib.get("href", "") if link_node is not None else ""
        summary = entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""
        published = entry.findtext("{http://www.w3.org/2005/Atom}published") or entry.findtext(
            "{http://www.w3.org/2005/Atom}updated"
        )
        if title and link:
            items.append(
                make_candidate(
                    title=title,
                    url=link,
                    source=source_name,
                    source_type=source_type,
                    published_at=published,
                    summary=summary,
                    metrics={},
                    raw={"fetch_type": "atom"},
                )
            )
    return items


def fetch_rss_sources(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    config = read_yaml(SOURCES_DIR / "rss_sources.yml", {"sources": []})
    candidates: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            LOGGER.info("RSS source disabled: %s", source.get("name"))
            continue
        if source.get("fetch_type", "rss") != "rss":
            LOGGER.info("RSS source placeholder skipped: %s", source.get("name"))
            continue
        try:
            text = request_text(source["url"], timeout=int(source.get("timeout", 15)), headers={"User-Agent": USER_AGENT})
            items = parse_rss_xml(text, source.get("name", "RSS"), source.get("source_type", "official"))
            filtered = [
                item
                for item in items
                if keyword_match(f"{item['title']} {item.get('summary', '')}", keywords)
                or item.get("source_type") == "official"
            ]
            LOGGER.info("%s fetched %s RSS candidates", source.get("name"), len(filtered))
            candidates.extend(filtered)
        except Exception as exc:
            LOGGER.warning("RSS source failed: %s (%s)", source.get("name"), exc)
    return candidates


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def history_delta(history: dict[str, Any], repo_name: str, report_date: str | None, field: str, days: int) -> int | str:
    if not report_date:
        return UNKNOWN
    repo_history = history.get(repo_name, {})
    today = repo_history.get(report_date)
    previous_date = (datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    previous = repo_history.get(previous_date)
    if not today or not previous:
        return UNKNOWN
    current_value = today.get(field)
    previous_value = previous.get(field)
    if current_value is None or previous_value is None:
        return UNKNOWN
    return max(0, int(current_value) - int(previous_value))


def update_repo_history(history: dict[str, Any], repo: dict[str, Any], report_date: str | None) -> None:
    if not report_date:
        return
    full_name = repo.get("full_name")
    if not full_name:
        return
    history.setdefault(full_name, {})[report_date] = {
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "open_issues": repo.get("open_issues_count"),
        "pushed_at": repo.get("pushed_at"),
        "created_at": repo.get("created_at"),
    }


def fetch_latest_release(repo_full_name: str, headers: dict[str, str]) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo_full_name}/releases/latest"
    try:
        import requests

        response = requests.get(url, timeout=12, headers=headers)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        LOGGER.info("GitHub latest release unavailable for %s: %s", repo_full_name, exc.__class__.__name__)
        return {}


def release_is_major(release: dict[str, Any]) -> bool:
    text = normalize_text(f"{release.get('name', '')} {release.get('tag_name', '')} {release.get('body', '')}").lower()
    terms = ["major", "v1", "v2", "launch", "agent", "mcp", "claude", "openai", "breaking", "workflow"]
    return any(term in text for term in terms)


def number_or_unknown(value: int | str) -> int | str:
    return value if value != UNKNOWN else UNKNOWN


def classify_github_signal(repo: dict[str, Any], latest_release: dict[str, Any], report_date: str | None) -> str:
    repo_age_days = days_between(report_date, repo.get("created_at")) if report_date else None
    stars = repo.get("stargazers_count") or 0
    star_delta_24h = repo.get("star_delta_24h", UNKNOWN)
    star_delta_7d = repo.get("star_delta_7d", UNKNOWN)
    fork_delta_7d = repo.get("fork_delta_7d", UNKNOWN)
    latest_release_at = latest_release.get("published_at")
    release_age_days = days_between(report_date, latest_release_at) if report_date and latest_release_at else None

    if repo_age_days is not None and repo_age_days <= 30 and (
        stars >= 50 or (isinstance(star_delta_7d, int) and star_delta_7d >= 30)
    ):
        return "new_project"
    if (
        isinstance(star_delta_24h, int)
        and star_delta_24h >= 20
        or isinstance(star_delta_7d, int)
        and star_delta_7d >= 80
        or isinstance(fork_delta_7d, int)
        and fork_delta_7d >= 20
    ):
        return "fast_growing"
    if release_age_days is not None and release_age_days <= 7:
        return "major_release"
    if latest_release and release_is_major(latest_release):
        return "major_release"
    if repo_age_days is not None and repo_age_days > 180 and stars >= 5000:
        return "mature_reference"
    if repo_age_days is not None and repo_age_days > 180:
        return "maintenance_update"
    return "new_project"


def github_search_queries(report_date: str | None) -> list[tuple[str, str, int, str]]:
    terms = [
        "llm agent",
        "ai coding",
        "mcp server",
        "rag",
        "multimodal ai",
        "local llm",
        "workflow agent",
        "claude code",
        "cursor ai",
    ]
    today = datetime.strptime(report_date, "%Y-%m-%d") if report_date else datetime.now(timezone.utc)
    date_30 = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_7 = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    queries: list[tuple[str, str, int, str]] = []
    for term in terms:
        queries.append((term, f"{term} created:>{date_30} stars:>20 archived:false fork:false", 8, "updated"))
        queries.append((term, f"{term} pushed:>{date_7} stars:50..5000 archived:false fork:false", 6, "updated"))
    for term in terms[:5]:
        queries.append((term, f"{term} stars:>5000 pushed:>{date_7} archived:false fork:false", 2, "updated"))
    return queries


def fetch_github_repos(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    headers = github_headers()
    has_github_token = bool(os.getenv("GITHUB_TOKEN"))
    candidates: list[dict[str, Any]] = []
    history_path = DATA_DIR / "repo_history.json"
    history = read_json(history_path, {})
    seen: set[str] = set()
    mature_reference_count = 0

    for term, raw_query, per_page, sort_field in github_search_queries(report_date):
        query = quote_plus(raw_query)
        url = f"https://api.github.com/search/repositories?q={query}&sort={sort_field}&order=desc&per_page={per_page}"
        try:
            data = request_json(url, timeout=20, headers=headers)
            for repo in data.get("items", []):
                full_name = repo.get("full_name") or repo.get("name") or ""
                if not full_name or full_name in seen:
                    continue
                if repo.get("archived") or repo.get("fork"):
                    continue
                text = f"{repo.get('name', '')} {repo.get('description', '')} {' '.join(repo.get('topics') or [])}"
                if not keyword_match(text, keywords):
                    continue
                update_repo_history(history, repo, report_date)
                repo["star_delta_24h"] = history_delta(history, full_name, report_date, "stars", 1)
                repo["star_delta_7d"] = history_delta(history, full_name, report_date, "stars", 7)
                repo["fork_delta_7d"] = history_delta(history, full_name, report_date, "forks", 7)
                latest_release: dict[str, Any] = {}
                signal_type = classify_github_signal(repo, latest_release, report_date)
                if has_github_token and signal_type not in {"new_project", "fast_growing"}:
                    latest_release = fetch_latest_release(full_name, headers)
                    signal_type = classify_github_signal(repo, latest_release, report_date)
                if signal_type == "maintenance_update":
                    continue
                if signal_type == "mature_reference":
                    if mature_reference_count >= 1:
                        continue
                    mature_reference_count += 1
                latest_release_at = latest_release.get("published_at")
                repo_age_days = days_between(report_date, repo.get("created_at")) if report_date else None
                seen.add(full_name)
                candidates.append(
                    make_candidate(
                        title=full_name,
                        url=repo.get("html_url", ""),
                        source="GitHub",
                        source_type="github",
                        published_at=repo.get("created_at") if signal_type == "new_project" else latest_release_at or repo.get("pushed_at"),
                        summary=repo.get("description") or "",
                        metrics={
                            "stars": repo.get("stargazers_count"),
                            "forks": repo.get("forks_count"),
                            "open_issues": repo.get("open_issues_count"),
                            "created_at": repo.get("created_at"),
                            "pushed_at": repo.get("pushed_at"),
                            "updated_at": repo.get("updated_at"),
                            "star_delta_24h": number_or_unknown(repo["star_delta_24h"]),
                            "star_delta_7d": number_or_unknown(repo["star_delta_7d"]),
                            "fork_delta_7d": number_or_unknown(repo["fork_delta_7d"]),
                            "latest_release_at": latest_release_at,
                            "repo_age_days": repo_age_days,
                            "signal_type": signal_type,
                            "recent_update": repo.get("pushed_at") or repo.get("updated_at"),
                        },
                        tags=repo.get("topics") or [],
                        raw={
                            "query": term,
                            "search_query": raw_query,
                            "created_at": repo.get("created_at"),
                            "pushed_at": repo.get("pushed_at"),
                            "updated_at": repo.get("updated_at"),
                            "stars": repo.get("stargazers_count"),
                            "forks": repo.get("forks_count"),
                            "open_issues": repo.get("open_issues_count"),
                            "topics": repo.get("topics") or [],
                            "language": repo.get("language"),
                            "archived": repo.get("archived"),
                            "fork": repo.get("fork"),
                            "default_branch": repo.get("default_branch"),
                            "description": repo.get("description"),
                            "latest_release_at": latest_release_at,
                            "latest_release_name": latest_release.get("name") or latest_release.get("tag_name"),
                            "star_delta_24h": number_or_unknown(repo["star_delta_24h"]),
                            "star_delta_7d": number_or_unknown(repo["star_delta_7d"]),
                            "fork_delta_7d": number_or_unknown(repo["fork_delta_7d"]),
                            "signal_type": signal_type,
                            "repo_age_days": repo_age_days,
                        },
                    )
                )
            LOGGER.info("GitHub query '%s' fetched %s candidates total so far", term, len(candidates))
        except Exception as exc:
            LOGGER.warning("GitHub source failed for query '%s': %s", term, exc)
    write_json(history_path, history)
    return candidates


def fetch_hacker_news(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    terms = ["AI OR LLM OR OpenAI OR Anthropic OR Claude OR ChatGPT OR agent OR Cursor OR MCP"]
    candidates: list[dict[str, Any]] = []
    for term in terms:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={quote_plus(term)}&tags=story&hitsPerPage=30"
        )
        try:
            data = request_json(url, timeout=20, headers={"User-Agent": USER_AGENT})
            for hit in data.get("hits", []):
                title = hit.get("title") or hit.get("story_title") or ""
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                text = f"{title} {hit.get('story_text') or ''}"
                if not keyword_match(text, keywords):
                    continue
                candidates.append(
                    make_candidate(
                        title=title,
                        url=link,
                        source="Hacker News",
                        source_type="hn",
                        published_at=hit.get("created_at"),
                        summary=normalize_text(hit.get("story_text") or ""),
                        metrics={"points": hit.get("points"), "comments": hit.get("num_comments")},
                        raw={
                            "hn_id": hit.get("objectID"),
                            "story_url": hit.get("url"),
                            "discussion_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        },
                    )
                )
            LOGGER.info("Hacker News fetched %s candidates", len(candidates))
        except Exception as exc:
            LOGGER.warning("Hacker News source failed: %s", exc)
    enrich_hn_comments(candidates)
    return candidates


def useful_comment(text: str) -> bool:
    text = normalize_text(text)
    if len(text) < 40:
        return False
    low_value = ["thanks for sharing", "great post", "this is awesome", "lol", "same here"]
    return not any(text.lower() == term for term in low_value)


def flatten_hn_comments(children: list[dict[str, Any]]) -> list[dict[str, str]]:
    comments: list[dict[str, str]] = []
    queue = list(children)
    while queue and len(comments) < 12:
        item = queue.pop(0)
        text = normalize_text(item.get("text") or "")
        if useful_comment(text):
            comments.append({"author": item.get("author") or "unknown", "text": text[:800]})
        queue.extend(item.get("children") or [])
    return comments


def enrich_hn_comments(candidates: list[dict[str, Any]]) -> None:
    minimum_points = int(os.getenv("HN_COMMENT_MIN_POINTS", "30"))
    minimum_comments = int(os.getenv("HN_COMMENT_MIN_COMMENTS", "10"))
    limit = int(os.getenv("SOCIAL_COMMENT_FETCH_LIMIT", "6"))
    qualified = [
        item for item in candidates
        if int(item.get("metrics", {}).get("points") or 0) >= minimum_points
        and int(item.get("metrics", {}).get("comments") or 0) >= minimum_comments
    ]
    qualified.sort(key=lambda item: int(item.get("metrics", {}).get("comments") or 0), reverse=True)
    for candidate in qualified[:limit]:
        hn_id = candidate.get("raw", {}).get("hn_id")
        try:
            payload = request_json(f"https://hn.algolia.com/api/v1/items/{hn_id}", timeout=15, headers={"User-Agent": USER_AGENT})
            comments = flatten_hn_comments(payload.get("children") or [])[:6]
            candidate["raw"]["top_comments"] = comments
            candidate["metrics"]["sampled_comments"] = len(comments)
        except Exception as exc:
            LOGGER.warning("HN comments unavailable for %s: %s", hn_id, exc.__class__.__name__)


def fetch_reddit(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    subreddits = ["LocalLLaMA", "MachineLearning", "OpenAI", "ClaudeAI", "ChatGPTCoding", "ArtificialInteligence"]
    candidates: list[dict[str, Any]] = []
    for subreddit in subreddits:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=20"
        try:
            data = request_json(url, timeout=20, headers={"User-Agent": USER_AGENT})
            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                title = post.get("title", "")
                text = f"{title} {post.get('selftext', '')}"
                if not keyword_match(text, keywords):
                    continue
                created = post.get("created_utc")
                published = datetime.fromtimestamp(created, tz=timezone.utc).isoformat() if created else None
                candidates.append(
                    make_candidate(
                        title=title,
                        url=f"https://www.reddit.com{post.get('permalink', '')}",
                        source=f"r/{subreddit}",
                        source_type="reddit",
                        published_at=published,
                        summary=post.get("selftext", ""),
                        metrics={"upvotes": post.get("ups"), "comments": post.get("num_comments")},
                        raw={
                            "reddit_id": post.get("id"),
                            "over_18": post.get("over_18"),
                            "is_self": post.get("is_self"),
                            "story_url": post.get("url_overridden_by_dest") or post.get("url"),
                            "discussion_url": f"https://www.reddit.com{post.get('permalink', '')}",
                        },
                    )
                )
            LOGGER.info("Reddit %s fetched candidates, total %s", subreddit, len(candidates))
        except Exception as exc:
            LOGGER.warning("Reddit source failed for r/%s: %s", subreddit, exc)
    enrich_reddit_comments(candidates)
    return candidates


def enrich_reddit_comments(candidates: list[dict[str, Any]]) -> None:
    minimum_upvotes = int(os.getenv("REDDIT_COMMENT_MIN_UPVOTES", "50"))
    minimum_comments = int(os.getenv("REDDIT_COMMENT_MIN_COMMENTS", "10"))
    limit = int(os.getenv("SOCIAL_COMMENT_FETCH_LIMIT", "6"))
    qualified = [
        item for item in candidates
        if int(item.get("metrics", {}).get("upvotes") or 0) >= minimum_upvotes
        and int(item.get("metrics", {}).get("comments") or 0) >= minimum_comments
    ]
    qualified.sort(key=lambda item: int(item.get("metrics", {}).get("comments") or 0), reverse=True)
    for candidate in qualified[:limit]:
        discussion_url = candidate.get("raw", {}).get("discussion_url", "").rstrip("/")
        try:
            payload = request_json(f"{discussion_url}.json?limit=12&sort=top&raw_json=1", timeout=15, headers={"User-Agent": USER_AGENT})
            children = payload[1].get("data", {}).get("children", []) if isinstance(payload, list) and len(payload) > 1 else []
            comments = []
            for child in children:
                data = child.get("data", {})
                text = normalize_text(data.get("body") or "")
                if useful_comment(text):
                    comments.append({"author": data.get("author") or "unknown", "score": data.get("score"), "text": text[:800]})
                if len(comments) >= 6:
                    break
            candidate["raw"]["top_comments"] = comments
            candidate["metrics"]["sampled_comments"] = len(comments)
        except Exception as exc:
            LOGGER.warning("Reddit comments unavailable for %s: %s", candidate.get("title"), exc.__class__.__name__)


def fetch_hugging_face(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    url = "https://huggingface.co/api/models?sort=trending&direction=-1&limit=30&full=true"
    candidates: list[dict[str, Any]] = []
    try:
        data = request_json(url, timeout=20, headers={"User-Agent": USER_AGENT})
        for model in data:
            model_id = model.get("modelId") or model.get("id") or ""
            tags = model.get("tags") or []
            text = f"{model_id} {' '.join(tags)} {model.get('pipeline_tag') or ''}"
            if not keyword_match(text, keywords) and not any(
                term in text.lower() for term in ["text-generation", "image-text-to-text", "llm", "qwen", "agent"]
            ):
                continue
            candidates.append(
                make_candidate(
                    title=model_id,
                    url=f"https://huggingface.co/{model_id}",
                    source="Hugging Face",
                    source_type="huggingface",
                    published_at=model.get("lastModified"),
                    summary=f"Pipeline: {model.get('pipeline_tag') or '未知'}",
                    metrics={
                        "likes": model.get("likes"),
                        "downloads": model.get("downloads"),
                        "discussion": model.get("discussionCount"),
                        "recent_update": model.get("lastModified"),
                    },
                    tags=tags,
                    raw={"pipeline_tag": model.get("pipeline_tag")},
                )
            )
        LOGGER.info("Hugging Face fetched %s candidates", len(candidates))
    except Exception as exc:
        LOGGER.warning("Hugging Face source failed: %s", exc)
    return candidates


def x_group_source_type(group_name: str) -> str:
    mapping = {
        "official_accounts": "x_official",
        "high_quality_ai_researchers": "x_researcher",
        "ai_builders": "x_builder",
        "ai_coding_tool_authors": "x_builder",
        "ai_product_observers": "x_product_observer",
        "chinese_ai_product_observers": "x_chinese_observer",
    }
    return mapping.get(group_name, "x_product_observer")


def x_heat(metrics: dict[str, Any]) -> int:
    return (
        int(metrics.get("like_count") or 0)
        + int(metrics.get("repost_count") or 0) * 2
        + int(metrics.get("reply_count") or 0) * 3
        + int(metrics.get("quote_count") or 0) * 2
    )


def x_candidate_allowed(source_type: str, heat: int, metrics: dict[str, Any], text: str, keywords: dict[str, list[str]]) -> bool:
    if source_type == "x_official":
        return keyword_match(text, keywords)
    if source_type == "x_chinese_observer":
        return heat >= 50 and keyword_match(text, keywords)
    return heat >= 100 or int(metrics.get("reply_count") or 0) >= 20 or int(metrics.get("quote_count") or 0) >= 10


def fetch_x_posts(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    token = os.getenv("X_BEARER_TOKEN")
    if not token:
        LOGGER.info("X_BEARER_TOKEN not configured, skip X fetch")
        return []

    config = read_yaml(SOURCES_DIR / "social_accounts.yml", {})
    candidates: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    start_dt = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0)
    end_dt = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(microsecond=0)
    if report_date:
        day = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_dt = day - timedelta(hours=8)
        end_dt = start_dt + timedelta(hours=48)

    for group_name, accounts in config.items():
        if not isinstance(accounts, list) or not accounts:
            continue
        handles = [
            item.get("handle") for item in accounts
            if isinstance(item, dict) and item.get("platform", "x") == "x" and item.get("handle")
        ]
        for chunk_start in range(0, len(handles), 8):
            chunk = handles[chunk_start : chunk_start + 8]
            from_query = " OR ".join(f"from:{handle}" for handle in chunk)
            if group_name == "chinese_ai_product_observers":
                topic_query = "(AI OR 大模型 OR 智能体 OR Agent OR 编程 OR Cursor OR Claude)"
            else:
                topic_query = "(AI OR LLM OR agent OR MCP OR Claude OR OpenAI OR Cursor)"
            query = f"({from_query}) {topic_query} -is:retweet -is:reply has:links"
            params = {
                "query": query,
                "max_results": "20",
                "tweet.fields": "created_at,public_metrics,author_id,entities,text,lang",
                "start_time": start_dt.isoformat().replace("+00:00", "Z"),
                "end_time": end_dt.isoformat().replace("+00:00", "Z"),
            }
            try:
                import requests

                response = requests.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers=headers,
                    params=params,
                    timeout=20,
                )
                response.raise_for_status()
                payload = response.json()
                source_type = x_group_source_type(group_name)
                for post in payload.get("data", []):
                    metrics = post.get("public_metrics") or {}
                    heat = x_heat(metrics)
                    text = normalize_text(post.get("text") or "")
                    if not x_candidate_allowed(source_type, heat, metrics, text, keywords):
                        continue
                    link = f"https://x.com/i/web/status/{post.get('id')}"
                    candidates.append(
                        make_candidate(
                            title=truncate_title(text),
                            url=link,
                            source=f"X/{group_name}",
                            source_type=source_type,
                            published_at=post.get("created_at"),
                            summary=text,
                            metrics={**metrics, "x_heat": heat},
                            tags=[group_name],
                            raw=post,
                        )
                    )
                LOGGER.info("X group %s fetched %s candidates total", group_name, len(candidates))
            except Exception as exc:
                LOGGER.warning("X fetch failed for group %s: %s", group_name, exc)
    return candidates


def truncate_title(text: str) -> str:
    text = normalize_text(text)
    return text[:80] + ("…" if len(text) > 80 else "")


def fetch_chinese_sources(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    config = read_yaml(SOURCES_DIR / "chinese_sources.yml", {"sources": []})
    candidates: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        try:
            text = request_text(source["url"], timeout=int(source.get("timeout", 15)), headers={"User-Agent": USER_AGENT})
            items = parse_rss_xml(text, source.get("name", "中文源"), source.get("source_type", "chinese_media"))
            filtered = [item for item in items if keyword_match(f"{item['title']} {item.get('summary', '')}", keywords)]
            for item in filtered:
                item["source_type"] = "chinese_media"
                item.setdefault("metrics", {})["signal_type"] = "中文社区信号"
            LOGGER.info("%s fetched %s Chinese candidates", source.get("name"), len(filtered))
            candidates.extend(filtered)
        except Exception as exc:
            LOGGER.warning("Chinese source failed: %s (%s)", source.get("name"), exc)

    xhs = read_yaml(SOURCES_DIR / "xhs_manual.yml", {"items": []})
    for item in xhs.get("items", []):
        if not item.get("note"):
            continue
        metrics = {
            "likes": item.get("likes"),
            "collects": item.get("collects"),
            "comments": item.get("comments"),
            "signal_type": "中文社区信号",
        }
        candidates.append(
            make_candidate(
                title=item.get("title") or "小红书手动信号",
                url=item.get("url") or "",
                source="小红书手动补充",
                source_type="chinese_community",
                published_at=item.get("added_at"),
                summary=item.get("note") or "",
                metrics=metrics,
                tags=["xiaohongshu", item.get("platform", "xiaohongshu")],
                raw=item,
            )
        )
    manual = read_yaml(SOURCES_DIR / "chinese_manual.yml", {"items": []})
    for item in manual.get("items", []):
        if not item.get("note") or not item.get("url"):
            continue
        candidates.append(
            make_candidate(
                title=item.get("title") or "中文观察者手动信号",
                url=item["url"],
                source=item.get("source") or "中文观察者手动补充",
                source_type="chinese_community",
                published_at=item.get("published_at") or item.get("added_at"),
                summary=item["note"],
                metrics={
                    "likes": item.get("likes"),
                    "collects": item.get("collects"),
                    "comments": item.get("comments"),
                    "signal_type": "中文社区信号",
                },
                tags=[item.get("platform", "manual"), item.get("account", "")],
                raw={**item, "fetch_type": "manual_fallback"},
            )
        )
    return candidates


def fetch_modelscope_placeholder() -> list[dict[str, Any]]:
    LOGGER.info("ModelScope fetch is a placeholder in MVP because stable unauthenticated API is not configured")
    return []


def fetch_all_sources(report_date: str | None = None) -> list[dict[str, Any]]:
    keywords = load_keywords()
    candidates: list[dict[str, Any]] = []
    fetchers = [
        ("rss", lambda: fetch_rss_sources(keywords, report_date)),
        ("chinese_sources", lambda: fetch_chinese_sources(keywords, report_date)),
        ("github", lambda: fetch_github_repos(keywords, report_date)),
        ("hacker_news", lambda: fetch_hacker_news(keywords, report_date)),
        ("reddit", lambda: fetch_reddit(keywords, report_date)),
        ("hugging_face", lambda: fetch_hugging_face(keywords, report_date)),
        ("x", lambda: fetch_x_posts(keywords, report_date)),
        ("modelscope", fetch_modelscope_placeholder),
    ]
    for name, fetcher in fetchers:
        before = len(candidates)
        try:
            candidates.extend(fetcher())
        except Exception:
            LOGGER.exception("Fetcher crashed unexpectedly: %s", name)
        LOGGER.info("Source %s added %s candidates", name, len(candidates) - before)
    return [candidate for candidate in candidates if candidate.get("title") and candidate.get("url")]
