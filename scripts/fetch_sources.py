from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

from utils import SOURCES_DIR, normalize_text, read_yaml, request_json, request_text, stable_id

LOGGER = logging.getLogger("ai_daily_brief")
USER_AGENT = "ai-daily-brief/0.1 (+https://github.com/)"


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


def fetch_github_repos(keywords: dict[str, list[str]], report_date: str | None = None) -> list[dict[str, Any]]:
    terms = ["llm agent", "ai coding", "mcp server", "rag", "multimodal ai", "local llm"]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    candidates: list[dict[str, Any]] = []
    if report_date:
        start_date = (datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=21)).strftime("%Y-%m-%d")
        pushed_filter = f"pushed:{start_date}..{report_date}"
    else:
        updated_after = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%d")
        pushed_filter = f"pushed:>{updated_after}"
    for term in terms:
        query = quote_plus(f"{term} {pushed_filter} stars:>50")
        url = f"https://api.github.com/search/repositories?q={query}&sort=updated&order=desc&per_page=10"
        try:
            data = request_json(url, timeout=20, headers=headers)
            for repo in data.get("items", []):
                text = f"{repo.get('name', '')} {repo.get('description', '')}"
                if not keyword_match(text, keywords):
                    continue
                candidates.append(
                    make_candidate(
                        title=repo.get("full_name") or repo.get("name") or "GitHub repository",
                        url=repo.get("html_url", ""),
                        source="GitHub",
                        source_type="github",
                        published_at=repo.get("pushed_at") or repo.get("updated_at"),
                        summary=repo.get("description") or "",
                        metrics={
                            "stars": repo.get("stargazers_count"),
                            "forks": repo.get("forks_count"),
                            "open_issues": repo.get("open_issues_count"),
                            "recent_update": repo.get("pushed_at") or repo.get("updated_at"),
                        },
                        tags=repo.get("topics") or [],
                        raw={"language": repo.get("language"), "query": term},
                    )
                )
            LOGGER.info("GitHub query '%s' fetched %s candidates total so far", term, len(candidates))
        except Exception as exc:
            LOGGER.warning("GitHub source failed for query '%s': %s", term, exc)
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
                        raw={"hn_id": hit.get("objectID")},
                    )
                )
            LOGGER.info("Hacker News fetched %s candidates", len(candidates))
        except Exception as exc:
            LOGGER.warning("Hacker News source failed: %s", exc)
    return candidates


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
                        raw={"over_18": post.get("over_18"), "is_self": post.get("is_self")},
                    )
                )
            LOGGER.info("Reddit %s fetched candidates, total %s", subreddit, len(candidates))
        except Exception as exc:
            LOGGER.warning("Reddit source failed for r/%s: %s", subreddit, exc)
    return candidates


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


def fetch_social_placeholders() -> list[dict[str, Any]]:
    config = read_yaml(SOURCES_DIR / "social_accounts.yml", {})
    account_count = sum(len(value or []) for value in config.values() if isinstance(value, list))
    LOGGER.info("Social account whitelist loaded: %s accounts; X fetch not enabled in MVP", account_count)
    return []


def fetch_modelscope_placeholder() -> list[dict[str, Any]]:
    LOGGER.info("ModelScope fetch is a placeholder in MVP because stable unauthenticated API is not configured")
    return []


def fetch_all_sources(report_date: str | None = None) -> list[dict[str, Any]]:
    keywords = load_keywords()
    candidates: list[dict[str, Any]] = []
    fetchers = [
        ("rss", lambda: fetch_rss_sources(keywords, report_date)),
        ("github", lambda: fetch_github_repos(keywords, report_date)),
        ("hacker_news", lambda: fetch_hacker_news(keywords, report_date)),
        ("reddit", lambda: fetch_reddit(keywords, report_date)),
        ("hugging_face", lambda: fetch_hugging_face(keywords, report_date)),
        ("social", fetch_social_placeholders),
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
