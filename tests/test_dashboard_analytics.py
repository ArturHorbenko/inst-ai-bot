import unittest
from types import SimpleNamespace

from video_processor.dashboard_analytics import DashboardAnalyticsClient


class DashboardAnalyticsClientTest(unittest.TestCase):
    def test_lists_reels_with_separate_read_secret(self):
        calls = []

        def transport(url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(is_success=True, json=lambda: {"ok": True, "reels": [{"media": {"id": "reel-1"}}]})

        client = DashboardAnalyticsClient("https://dashboard.example/", "read-secret", transport=transport)
        self.assertEqual(client.list_recent_reels(5), [{"media": {"id": "reel-1"}}])
        self.assertEqual(calls[0][0], "https://dashboard.example/api/internal/mcp/reels")
        self.assertEqual(calls[0][1]["headers"], {"X-MCP-Read-Secret": "read-secret"})
        self.assertEqual(calls[0][1]["params"], {"limit": 5})

    def test_rejects_missing_dashboard_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            DashboardAnalyticsClient(None, None).get_reel_analytics("reel-1")

    def test_reads_an_n_day_content_audit(self):
        def transport(url, **kwargs):
            self.assertEqual(url, "https://dashboard.example/api/internal/mcp/audit")
            self.assertEqual(kwargs["params"], {"days": 14})
            return SimpleNamespace(is_success=True, json=lambda: {"ok": True, "audit": {"window": {"days": 14}}})

        audit = DashboardAnalyticsClient("https://dashboard.example", "read-secret", transport=transport).get_content_audit(14)
        self.assertEqual(audit["window"]["days"], 14)

    def test_reads_current_creator_profile_with_dashboard_read_secret(self):
        def transport(url, **kwargs):
            self.assertEqual(url, "https://dashboard.example/api/internal/mcp/profile")
            self.assertEqual(kwargs["headers"], {"X-MCP-Read-Secret": "read-secret"})
            self.assertEqual(kwargs["params"], {"days": 60})
            return SimpleNamespace(is_success=True, json=lambda: {"ok": True, "profile": {"window": {"days": 60}}})

        profile = DashboardAnalyticsClient(
            "https://dashboard.example", "read-secret", transport=transport
        ).get_current_creator_profile(60)

        self.assertEqual(profile, {"window": {"days": 60}})

    def test_surfaces_dashboard_errors(self):
        response = SimpleNamespace(is_success=False, status_code=404, json=lambda: {"ok": False, "error": "Reel not found."})
        with self.assertRaisesRegex(RuntimeError, "Reel not found"):
            DashboardAnalyticsClient("https://dashboard.example", "read-secret", transport=lambda *_args, **_kwargs: response).get_reel_analytics("missing")
