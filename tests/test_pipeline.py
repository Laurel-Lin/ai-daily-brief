from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_sources import enrich_hn_comments, enrich_reddit_comments, flatten_hn_comments  # noqa: E402
from filter_score import deduplicate, feedback_result  # noqa: E402
from summarize import build_product_opportunity, community_insights, render_markdown  # noqa: E402
from utils import candidate_history_keys  # noqa: E402
import main as main_module  # noqa: E402


def candidate(**overrides):
    item = {
        "id": "official-1",
        "title": "Acme launches Agent Studio for coding workflows",
        "url": "https://acme.example/blog/agent-studio",
        "source": "Acme",
        "source_type": "official",
        "published_at": "2026-07-12T01:00:00+00:00",
        "summary": "Agent Studio connects repository context, terminal tools and CI checks for software teams.",
        "metrics": {},
        "tags": ["agent", "coding"],
        "raw": {},
        "score": 88,
        "readable_value": 90,
        "score_breakdown": {"product_inspiration": 90},
    }
    item.update(overrides)
    return item


class PipelineTests(unittest.TestCase):
    def test_cross_source_story_prefers_official_and_keeps_discussion(self):
        official = candidate()
        hn = candidate(
            id="hn-1",
            title="Acme launches Agent Studio for coding workflows",
            source="Hacker News",
            source_type="hn",
            score=82,
            metrics={"points": 180, "comments": 64, "sampled_comments": 1},
            raw={
                "story_url": official["url"],
                "discussion_url": "https://news.ycombinator.com/item?id=1",
                "top_comments": [{"author": "dev", "text": "The audit trail is useful, but terminal permissions still need explicit controls."}],
            },
        )
        merged = deduplicate([hn, official])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_type"], "official")
        self.assertEqual(merged[0]["related_sources"][0]["source_type"], "hn")
        self.assertEqual(merged[0]["community_discussions"][0]["metrics"]["points"], 180)

    def test_title_similarity_merges_without_equal_url(self):
        left = candidate(url="https://acme.example/launch")
        right = candidate(id="reddit-1", url="https://reddit.com/r/ai/1", source_type="reddit", source="r/ai")
        self.assertEqual(len(deduplicate([left, right])), 1)

    def test_similar_chinese_titles_merge(self):
        left = candidate(title="Acme 正式发布 Agent Studio 编程工作流", url="https://acme.example/cn")
        right = candidate(
            id="reddit-cn",
            title="Acme 发布 Agent Studio 编程工作流",
            url="https://reddit.com/r/ai/cn",
            source="r/ai",
            source_type="reddit",
        )
        self.assertEqual(len(deduplicate([left, right])), 1)

    def test_hn_comment_filter_skips_short_noise(self):
        comments = flatten_hn_comments([
            {"author": "a", "text": "Great post", "children": []},
            {"author": "b", "text": "The permission model is unclear and would block adoption in regulated teams.", "children": []},
        ])
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author"], "b")

    def test_hn_comment_api_response_is_attached(self):
        item = candidate(
            source="Hacker News",
            source_type="hn",
            metrics={"points": 80, "comments": 20},
            raw={"hn_id": "42", "discussion_url": "https://news.ycombinator.com/item?id=42"},
        )
        payload = {
            "children": [{
                "author": "builder",
                "text": "The tool is promising, although permission controls need to be much more explicit.",
                "children": [],
            }]
        }
        with patch("fetch_sources.request_json", return_value=payload):
            enrich_hn_comments([item])
        self.assertEqual(item["metrics"]["sampled_comments"], 1)
        self.assertEqual(item["raw"]["top_comments"][0]["author"], "builder")

    def test_reddit_top_comment_api_response_is_attached(self):
        item = candidate(
            source="r/LocalLLaMA",
            source_type="reddit",
            metrics={"upvotes": 120, "comments": 40},
            raw={"discussion_url": "https://www.reddit.com/r/LocalLLaMA/comments/abc/example"},
        )
        payload = [{}, {"data": {"children": [{"data": {
            "author": "local_user",
            "score": 25,
            "body": "Local deployment is useful, but memory usage remains too high for common laptops.",
        }}]}}]
        with patch("fetch_sources.request_json", return_value=payload):
            enrich_reddit_comments([item])
        self.assertEqual(item["metrics"]["sampled_comments"], 1)
        self.assertEqual(item["raw"]["top_comments"][0]["score"], 25)

    def test_cluster_history_includes_alternate_source(self):
        item = candidate(
            related_sources=[{
                "title": "HN discusses Acme Agent Studio",
                "url": "https://news.ycombinator.com/item?id=1",
                "source_type": "hn",
            }]
        )
        keys = candidate_history_keys(item)
        self.assertIn("url:https://news.ycombinator.com/item?id=1", keys)
        self.assertIn("title:hn discusses acme agent studio", keys)

    def test_feedback_adjustment_and_rejection(self):
        config = {
            "rules": [
                {"field": "source", "match": "Acme", "rating": "useful", "adjustment": 8, "note": "trusted"},
                {"field": "title", "match": "Agent Studio", "rating": "avoid", "note": "already evaluated"},
            ]
        }
        with patch("filter_score.read_yaml", return_value=config):
            adjustment, notes, rejected = feedback_result(candidate())
        self.assertEqual(adjustment, -17)
        self.assertTrue(rejected)
        self.assertEqual(notes, ["trusted", "already evaluated"])

    def test_product_opportunity_is_actionable(self):
        result = build_product_opportunity([candidate()])
        self.assertIn("目标用户", result)
        self.assertIn("最小验证", result)
        self.assertNotIn("观察它解决的具体工作流", " ".join(result.values()))

    def test_markdown_contains_new_sections_and_real_discussion(self):
        item = candidate(
            community_discussions=[{
                "source": "Hacker News",
                "source_type": "hn",
                "url": "https://news.ycombinator.com/item?id=1",
                "metrics": {"points": 100, "comments": 30},
                "comments": [{"text": "Teams like the audit trail, but they still need explicit terminal permission controls."}],
            }]
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            output = render_markdown("2026-07-12", [item], {"raw_candidates": 2, "scored_candidates": 2, "selected_candidates": 1})
        self.assertIn("## 今日产品机会", output)
        self.assertIn("社区讨论：", output)
        self.assertIn("terminal permission controls", output)

    def test_main_runs_end_to_end_without_network_or_push(self):
        class Logger:
            def info(self, *args, **kwargs):
                pass

        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            digest_dir = root / "digests"
            history_file = data_dir / "selected_history.json"
            with (
                patch.object(main_module, "DATA_DIR", data_dir),
                patch.object(main_module, "DIGESTS_DIR", digest_dir),
                patch.object(main_module, "SELECTED_HISTORY_FILE", history_file),
                patch.object(main_module, "fetch_all_sources", return_value=[candidate()]),
                patch.object(main_module, "push_serverchan", return_value=True) as push_mock,
                patch.object(main_module, "setup_logging", return_value=Logger()),
                patch.object(main_module, "today_string", return_value="2026-07-12"),
                patch.dict(os.environ, {"OPENAI_API_KEY": "", "MIN_SCORE": "75", "MAX_ITEMS": "8"}),
            ):
                result = main_module.main()
            self.assertEqual(result, 0)
            self.assertTrue((digest_dir / "2026-07-12.md").exists())
            self.assertTrue((data_dir / "latest.json").exists())
            self.assertTrue((data_dir / "raw_candidates.json").exists())
            self.assertTrue(history_file.exists())
            push_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
