import pytest

from video_processor import social_status


class FakeYoutubeDL:
    last_opts = None

    def __init__(self, opts):
        FakeYoutubeDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        assert url == 'https://www.instagram.com/reel/ABC123/'
        assert download is False
        return {
            'id': 'ABC123',
            'webpage_url': url,
            'description': 'demo caption #AI #Demo',
            'uploader': 'creator',
            'uploader_id': '42',
            'like_count': 100,
            'view_count': 2000,
            'comment_count': 3,  # stale/unreliable from yt-dlp; web API overrides
            'timestamp': 1710000000,
        }


def _fake_comments(total=1920):
    def _impl(shortcode, cookie_path, max_comments, referer):
        assert shortcode == 'ABC123'
        comments = [
            {'author': 'a', 'text': 'first', 'timestamp': 1, 'like_count': 50, 'reply_count': 2,
             'replies': [{'author': 'r1', 'text': 'reply one', 'timestamp': 2, 'like_count': 1}]},
            {'author': 'b', 'text': 'second', 'timestamp': 3, 'like_count': 10, 'reply_count': 0, 'replies': []},
        ][:max_comments]
        return comments, total
    return _impl


@pytest.fixture
def cookie_env(monkeypatch, tmp_path):
    cookie_file = tmp_path / 'instagram-cookies.txt'
    cookie_file.write_text('# Netscape HTTP Cookie File\n')
    monkeypatch.setenv('INSTAGRAM_COOKIES_FILE', str(cookie_file))
    return cookie_file


def test_fetch_status_returns_metadata_and_ranked_comments(monkeypatch, cookie_env):
    monkeypatch.setattr(social_status, 'YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr(social_status, '_fetch_comments_via_web_api', _fake_comments(total=1920))

    status = social_status.fetch_instagram_post_status(
        'https://www.instagram.com/reel/ABC123/',
        max_comments=2,
    )

    # yt-dlp must not be asked for comments anymore
    assert FakeYoutubeDL.last_opts['getcomments'] is False
    assert status['id'] == 'ABC123'
    assert status['like_count'] == 100
    # web API comment_count overrides yt-dlp's stale value
    assert status['comment_count'] == 1920
    assert status['comments_returned'] == 2
    assert status['comments'][0]['author'] == 'a'
    assert status['comments'][0]['replies'][0]['text'] == 'reply one'
    assert 'comments_error' not in status


def test_fetch_status_respects_max_comments(monkeypatch, cookie_env):
    monkeypatch.setattr(social_status, 'YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr(social_status, '_fetch_comments_via_web_api', _fake_comments())

    status = social_status.fetch_instagram_post_status(
        'https://www.instagram.com/reel/ABC123/',
        max_comments=1,
    )
    assert status['comments_returned'] == 1


def test_fetch_status_can_skip_comments(monkeypatch, cookie_env):
    monkeypatch.setattr(social_status, 'YoutubeDL', FakeYoutubeDL)

    def _boom(*a, **k):
        raise AssertionError('comments should not be fetched')

    monkeypatch.setattr(social_status, '_fetch_comments_via_web_api', _boom)

    status = social_status.fetch_instagram_post_status(
        'https://www.instagram.com/reel/ABC123/',
        include_comments=False,
    )
    assert status['comments'] == []
    assert status['comments_returned'] == 0
    assert status['comment_count'] == 3  # falls back to yt-dlp value


def test_fetch_status_degrades_when_cookies_missing(monkeypatch):
    monkeypatch.delenv('INSTAGRAM_COOKIES_FILE', raising=False)
    monkeypatch.setattr(social_status, 'YoutubeDL', FakeYoutubeDL)

    status = social_status.fetch_instagram_post_status(
        'https://www.instagram.com/reel/ABC123/',
    )
    assert status['comments'] == []
    assert 'INSTAGRAM_COOKIES_FILE not set' in status['comments_error']
    # metadata still returned
    assert status['like_count'] == 100


def test_fetch_status_degrades_on_comments_http_error(monkeypatch, cookie_env):
    import urllib.error

    monkeypatch.setattr(social_status, 'YoutubeDL', FakeYoutubeDL)

    def _raise(*a, **k):
        raise urllib.error.HTTPError('url', 429, 'Too Many Requests', {}, None)

    monkeypatch.setattr(social_status, '_fetch_comments_via_web_api', _raise)

    status = social_status.fetch_instagram_post_status(
        'https://www.instagram.com/reel/ABC123/',
    )
    assert status['comments'] == []
    assert 'comments fetch failed' in status['comments_error']
    assert status['like_count'] == 100  # metadata survives


def test_fetch_status_rejects_missing_cookie_file(monkeypatch):
    monkeypatch.setenv('INSTAGRAM_COOKIES_FILE', '/tmp/missing-instagram-cookies.txt')

    with pytest.raises(ValueError, match='INSTAGRAM_COOKIES_FILE does not exist'):
        social_status.fetch_instagram_post_status('https://www.instagram.com/reel/ABC123/')


def test_fetch_status_rejects_non_instagram_urls():
    with pytest.raises(ValueError, match='Not an Instagram'):
        social_status.fetch_instagram_post_status('https://example.com/video')


def test_to_status_snapshot_adds_shortcode_and_timestamp():
    status = {'url': 'https://www.instagram.com/reel/ABC123/', 'like_count': 5}
    snap = social_status.to_status_snapshot(status, fetched_at='2026-06-27T00:00:00Z')
    assert snap['shortcode'] == 'ABC123'
    assert snap['fetched_at'] == '2026-06-27T00:00:00Z'
    assert snap['like_count'] == 5
    # original is not mutated
    assert 'shortcode' not in status


def test_shortcode_to_media_id_roundtrip():
    assert social_status._shortcode_to_media_id('DaDb2hFxeI6') == 3928105793635082810


def test_shortcode_from_url_variants():
    f = social_status._shortcode_from_url
    assert f('https://www.instagram.com/reel/ABC123/') == 'ABC123'
    assert f('https://www.instagram.com/p/XYZ789/') == 'XYZ789'
    assert f('https://www.instagram.com/reels/QQQ/') == 'QQQ'
