import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar import __main__, aisa, video_radar as vr, video_sources as vs


class VideoRadarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 29, 18, tzinfo=timezone.utc)
        self.cfg = copy.deepcopy(vr.DEFAULTS)

    def raw(self, i=1, **extra):
        return {"id": str(7000000000000000000+i), "url": f"https://www.tiktok.com/@creator{i}/video/{7000000000000000000+i}",
                "author": f"creator{i}", "caption": "AI photo template #MoonWalkEffect", "views": 200000,
                "published_at": vr.iso(self.now-timedelta(hours=12)), "observed_at": vr.iso(self.now),
                "terms": ["moon walk effect"], "comments": [{"id": f"c{i}", "text": "What app is this?"}], **extra}

    def run_scan(self, rows, **kwargs):
        return vr.scan(self.cfg, self.root / "state", imported=rows, offline=True, now=self.now, **kwargs)

    def test_three_independent_authors_and_demand_prioritized_for_review(self):
        result = self.run_scan([self.raw(i) for i in range(3)])
        r = result["candidates"][0]
        self.assertEqual(r["status"], "priority_review")
        self.assertEqual(r["hit_authors"], 3)
        self.assertFalse(r["same_effect_confirmed"])
        self.assertIn("同款效果", " ".join(r["gaps"]))

    def test_duplicate_video_and_same_author_never_prove_replication(self):
        result = self.run_scan([self.raw(1), self.raw(1), self.raw(2, author="creator1"), self.raw(3, author="creator1")])
        r = result["candidates"][0]
        self.assertEqual(result["video_count"], 3)
        self.assertEqual(r["recent_authors"], 1)
        self.assertEqual(r["status"], "watch")
        self.assertEqual(len(r["demand"]), 3)

    def test_missing_unknown_future_and_stale_metrics_not_scored(self):
        rows = [self.raw(1, published_at=None), self.raw(2, published_at=vr.iso(self.now+timedelta(hours=1))),
                self.raw(3, observed_at=vr.iso(self.now-timedelta(days=2))), self.raw(4, views=None)]
        r = self.run_scan(rows)["candidates"][0]
        self.assertEqual(r["recent_videos"], 1)
        self.assertEqual(r["hit_authors"], 0)
        self.assertIsNone(r["videos"][0]["views"])

    def test_seeds_are_only_calibration_not_new_opportunities(self):
        rows = [self.raw(i, caption="AI #hotelLobby", terms=["hotel lobby"]) for i in range(3)]
        result = self.run_scan(rows)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["seed_reference"][0]["status"], "seed")

    def test_query_context_not_a_keyword_assignment(self):
        v = vr.normalize(self.raw(caption="some unrelated video #fyp", terms=[], queries=["new viral effect"]), self.now)
        self.assertEqual(vr.extract_terms(v, self.cfg), [])

    def test_missing_comments_does_not_claim_demand(self):
        r = self.run_scan([self.raw(i, comments=[]) for i in range(3)])["candidates"][0]
        self.assertEqual(r["status"], "watch")
        self.assertEqual(r["demand"], [])

    def test_demand_deduplication_and_manual_confirmation(self):
        r = self.run_scan([self.raw(i, template_confirmed=True, comments=[{"id": "one", "text": "How to do this?"}])
                           for i in range(3)])["candidates"][0]
        self.assertEqual(len(r["demand"]), 1)
        self.assertTrue(r["same_effect_confirmed"])

    def test_growth_requires_previous_real_sample(self):
        first = self.raw()
        r = self.run_scan([first])["candidates"][0]
        self.assertIsNone(r["videos"][0]["views_per_hour_since_previous_scan"])
        later = self.now+timedelta(hours=2)
        second = {**first, "observed_at": vr.iso(later), "views": 220000}
        r = vr.scan(self.cfg, self.root/"state", [second], True, later)["candidates"][0]
        self.assertEqual(r["videos"][0]["views_per_hour_since_previous_scan"], 10000)
        self.assertEqual(r["first_seen"], vr.iso(self.now))

    def test_merge_keeps_latest_metrics_and_deduplicates_comments(self):
        a = vr.normalize(self.raw(), self.now)
        b = vr.normalize(self.raw(views=10, observed_at=vr.iso(self.now-timedelta(hours=1))), self.now)
        videos = {}
        vr.merge_video(videos, a)
        vr.merge_video(videos, b)
        self.assertEqual(videos[a["id"]]["views"], 200000)
        self.assertEqual(len(videos[a["id"]]["comments"]), 1)

    def test_native_tikhub_payload_and_url_validation(self):
        v = vr.normalize({"aweme_id": "123", "author": {"unique_id": "test"}, "desc": "hi",
                          "create_time": 1793296800, "statistics": {"play_count": 12345}}, self.now)
        self.assertEqual(v["views"], 12345)
        self.assertIn("@test/video/123", v["url"])
        self.assertIsNone(vr.normalize(self.raw(url="javascript:alert(1)"), self.now))
        self.assertIsNone(vr.normalize(self.raw(url="https://tiktok.com.evil.test/video/123"), self.now))

    def test_source_adapter_rejects_unknown_shapes(self):
        client = vs.TikHub()
        with patch.object(client, "get", return_value={"unexpected": []}):
            with self.assertRaises(vs.SourceError): client.search("ai", "US", 7)
        with patch.object(client, "get", return_value={"aweme_list": []}):
            self.assertEqual(client.search("ai", "US", 7), [])
        with patch.object(client, "get", return_value={"data": [{"aweme_info": {"aweme_id": "123"}}]}):
            self.assertEqual(client.search("ai", "US", 7)[0]["aweme_id"], "123")

    def test_request_cap_and_missing_key_do_not_call_network(self):
        client = vs.TikHub(1)
        with patch.dict("os.environ", {"TIKHUB_API_KEY": ""}):
            with self.assertRaises(vs.SourceError): client.search("ai", "US", 7)
        client.calls = 1
        with patch.dict("os.environ", {"TIKHUB_API_KEY": "test"}):
            with self.assertRaises(vs.SourceError): client.search("ai", "US", 7)

    def test_blocked_live_scan_is_visible(self):
        with patch.dict("os.environ", {"TIKHUB_API_KEY": "", "AISA_API_KEY": ""}):
            out = vr.scan(self.cfg, self.root/"state", now=self.now)
        self.assertEqual(out["status"], "blocked")
        self.assertGreater(len(out["errors"]), 0)

    def test_offline_scan_never_calls_a_paid_provider(self):
        with patch.object(vs.TikHub, "get", side_effect=AssertionError("network")), patch.object(aisa, "call", side_effect=AssertionError("paid")):
            self.run_scan([self.raw()])

    def test_pick_and_reject_survive_scans(self):
        self.run_scan([self.raw()])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vr.main(["pick", "moon walk effect", "--state", str(self.root/"state")]), 0)
        self.assertEqual(self.run_scan([self.raw()])["candidates"][0]["decision"], "picked")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vr.main(["reject", "moon walk effect", "--state", str(self.root/"state")]), 0)
        self.assertEqual(self.run_scan([self.raw()])["candidates"][0]["decision"], "rejected")

    def test_html_and_csv_untrusted_content_is_inert(self):
        rows = [self.raw(terms=['=cmd|test', '</script><img src=x onerror=alert(1)>'])]
        out = self.run_scan(rows)
        directory = self.root/"report"
        vr.write_report(out, directory)
        page = (directory/"index.html").read_text()
        self.assertNotIn('</script><img', page)
        self.assertIn('\\u003c/script', page)
        self.assertIn("'=cmd|test", (directory/"keywords.csv").read_text())
        self.assertEqual(len(list(directory.iterdir())), 4)

    def test_cli_dispatch_demo_and_isolated_state(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = __main__.main(["videos", "demo", "--state", str(self.root/"state"), "--out", str(self.root/"demo")])
        self.assertEqual(code, 0)
        out = json.loads((self.root/"demo"/"report.json").read_text())
        self.assertEqual(out["mode"], "SYNTHETIC_DEMO")
        self.assertFalse((self.root/"state"/"video-radar.db").exists())

    def test_configuration_rejects_negative_request_cap(self):
        config = self.root/"config.json"
        config.write_text('{"max_tikhub_requests": -1}')
        with self.assertRaises(ValueError): vr.load_config(config)

    def test_live_adapter_path_and_optional_research(self):
        self.cfg["discovery_queries"] = ["new ai effect"]
        self.cfg["seed_terms"] = []
        self.cfg["research"]["max_candidates"] = 1
        with patch.dict("os.environ", {"TIKHUB_API_KEY": "test", "AISA_API_KEY": "test"}), \
             patch.object(vs.TikHub, "search", return_value=[self.raw(i) for i in range(3)]), \
             patch.object(vs.TikHub, "comments", return_value=[{"id": "abc", "text": "What app?", "author": "user"}]), \
             patch.object(vs, "search_web", return_value=([{"kind": "tool", "url": "https://example.com", "title": "Tool"}], 10)), \
             patch.object(vs, "trends", return_value=({"status": "no_data"}, 20)):
            out = vr.scan(self.cfg, self.root/"state", now=self.now)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["aisa_reported_cost_micros"], 30)
        self.assertEqual(out["candidates"][0]["competition"]["tool_pages"], 1)


if __name__ == "__main__":
    unittest.main()
