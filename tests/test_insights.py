"""Unit tests for the Instagram insights integration.

Network-free: the Graph API transport (`_graph_get`) and token exchange
(`_exchange_token`) are patched out. Run with:

    venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from video_processor.downloader import extract_shortcode
from video_processor import insights
from video_processor.insights import (
    InsightsError,
    ReelNotOwned,
    _ensure_fresh_token,
    _fingerprint,
    _permalink_has_shortcode,
    fetch_media_insights,
    resolve_media_id,
)


def _config(**overrides):
    base = dict(
        META_APP_ID="app-id",
        META_APP_SECRET="app-secret",
        META_GRAPH_TOKEN="env-token",
        INSTAGRAM_USER_ID="17841405595099911",
        META_GRAPH_VERSION="v21.0",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeInsightsStore:
    def __init__(self, snapshots=None):
        self._snaps = list(snapshots or [])

    def insert(self, snapshot):
        self._snaps.append(snapshot)
        return snapshot

    def list(self, media_id=None):
        return [s for s in self._snaps if media_id is None or s["media_id"] == media_id]

    def find_media_id(self, shortcode):
        for s in self._snaps:
            if s.get("shortcode") == shortcode:
                return s["media_id"]
        return None


class FakeCredsStore:
    def __init__(self, doc=None):
        self._doc = doc

    def get(self):
        return dict(self._doc) if self._doc else None

    def put(self, access_token, expires_at, bootstrap_fingerprint):
        self._doc = {
            "access_token": access_token,
            "expires_at": expires_at,
            "bootstrap_fingerprint": bootstrap_fingerprint,
        }


class ExtractShortcodeTest(unittest.TestCase):
    def test_reel_url(self):
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/reel/Cabc123_-/"), "Cabc123_-"
        )

    def test_reels_plural_url(self):
        self.assertEqual(
            extract_shortcode("https://instagram.com/reels/Cabc123/"), "Cabc123"
        )

    def test_post_url_with_query(self):
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/p/Cabc123/?igsh=xyz"), "Cabc123"
        )

    def test_non_instagram_url_rejected(self):
        with self.assertRaises(ValueError):
            extract_shortcode("https://example.com/reel/Cabc123/")

    def test_profile_url_rejected(self):
        with self.assertRaises(ValueError):
            extract_shortcode("https://www.instagram.com/someuser/")


class HelperTest(unittest.TestCase):
    def test_fingerprint_stable_and_distinct(self):
        self.assertEqual(_fingerprint("token-a"), _fingerprint("token-a"))
        self.assertNotEqual(_fingerprint("token-a"), _fingerprint("token-b"))
        self.assertEqual(len(_fingerprint("token-a")), 16)

    def test_permalink_has_shortcode(self):
        self.assertTrue(
            _permalink_has_shortcode("https://www.instagram.com/reel/Cabc/", "Cabc")
        )
        self.assertFalse(
            _permalink_has_shortcode("https://www.instagram.com/reel/Cabc/", "Cxyz")
        )
        self.assertFalse(_permalink_has_shortcode("", "Cabc"))


class EnsureFreshTokenTest(unittest.TestCase):
    def test_missing_token_raises(self):
        with self.assertRaises(InsightsError):
            _ensure_fresh_token(_config(META_GRAPH_TOKEN=None), FakeCredsStore())

    def test_missing_app_secret_raises(self):
        with self.assertRaises(InsightsError):
            _ensure_fresh_token(_config(META_APP_SECRET=None), FakeCredsStore())

    def test_bootstrap_when_no_creds(self):
        creds = FakeCredsStore()
        future = datetime.now(timezone.utc) + timedelta(days=60)
        with patch.object(insights, "_exchange_token", return_value=("fresh", future)) as ex:
            token = _ensure_fresh_token(_config(), creds)
        self.assertEqual(token, "fresh")
        ex.assert_called_once()
        self.assertEqual(creds.get()["bootstrap_fingerprint"], _fingerprint("env-token"))

    def test_cached_valid_token_not_refreshed(self):
        creds = FakeCredsStore({
            "access_token": "stored",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=40),
            "bootstrap_fingerprint": _fingerprint("env-token"),
        })
        with patch.object(insights, "_exchange_token") as ex:
            token = _ensure_fresh_token(_config(), creds)
        self.assertEqual(token, "stored")
        ex.assert_not_called()

    def test_refresh_when_near_expiry(self):
        creds = FakeCredsStore({
            "access_token": "stale",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=2),
            "bootstrap_fingerprint": _fingerprint("env-token"),
        })
        future = datetime.now(timezone.utc) + timedelta(days=60)
        with patch.object(insights, "_exchange_token", return_value=("renewed", future)) as ex:
            token = _ensure_fresh_token(_config(), creds)
        self.assertEqual(token, "renewed")
        ex.assert_called_once()

    def test_reseed_when_env_token_changed(self):
        creds = FakeCredsStore({
            "access_token": "stored",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=40),
            "bootstrap_fingerprint": _fingerprint("a-different-old-token"),
        })
        future = datetime.now(timezone.utc) + timedelta(days=60)
        with patch.object(insights, "_exchange_token", return_value=("reseeded", future)) as ex:
            token = _ensure_fresh_token(_config(), creds)
        self.assertEqual(token, "reseeded")
        ex.assert_called_once()
        self.assertEqual(creds.get()["bootstrap_fingerprint"], _fingerprint("env-token"))


class ResolveMediaIdTest(unittest.TestCase):
    def test_cache_hit_skips_api(self):
        store = FakeInsightsStore([{"shortcode": "Cabc", "media_id": "111"}])
        with patch.object(insights, "_graph_get") as gg:
            media_id = resolve_media_id(_config(), "tok", "Cabc", store)
        self.assertEqual(media_id, "111")
        gg.assert_not_called()

    def test_paginates_until_match(self):
        page1 = {
            "data": [{"id": "1", "permalink": "https://www.instagram.com/reel/Cother/"}],
            "paging": {"next": "https://graph.facebook.com/next-page"},
        }
        page2 = {
            "data": [{"id": "2", "permalink": "https://www.instagram.com/reel/Cwant/"}],
        }
        with patch.object(insights, "_graph_get", side_effect=[page1, page2]):
            media_id = resolve_media_id(_config(), "tok", "Cwant", FakeInsightsStore())
        self.assertEqual(media_id, "2")

    def test_not_owned_raises(self):
        page = {"data": [{"id": "1", "permalink": "https://www.instagram.com/reel/Cother/"}]}
        with patch.object(insights, "_graph_get", return_value=page):
            with self.assertRaises(ReelNotOwned):
                resolve_media_id(_config(), "tok", "Cmissing", FakeInsightsStore())


class FetchMediaInsightsTest(unittest.TestCase):
    def test_merges_base_fields_and_insights_edge(self):
        base = {
            "permalink": "https://www.instagram.com/reel/Cabc/",
            "caption": "a caption",
            "timestamp": "2026-05-01T12:00:00+0000",
            "media_product_type": "REELS",
            "like_count": 300,
            "comments_count": 12,
        }
        edge = {
            "data": [
                {"name": "views", "values": [{"value": 12345}]},
                {"name": "reach", "values": [{"value": 9000}]},
            ]
        }
        with patch.object(insights, "_graph_get", side_effect=[base, edge]):
            result = fetch_media_insights(_config(), "tok", "media-1")
        self.assertEqual(result["permalink"], base["permalink"])
        self.assertEqual(result["posted_at"], base["timestamp"])
        self.assertEqual(
            result["metrics"],
            {"likes": 300, "comments": 12, "views": 12345, "reach": 9000},
        )


if __name__ == "__main__":
    unittest.main()
