from pathlib import Path
from unittest.mock import patch

from video_processor.downloader import download_instagram_reel


def test_uses_configured_cookie_file_for_instagram_download(tmp_path: Path):
    cookie_file = tmp_path / 'instagram-cookies.txt'
    cookie_file.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    output = tmp_path / 'DbdjZO6RYXw.mp4'

    class FakeYoutubeDL:
        options = None

        def __init__(self, options):
            type(self).options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            output.write_bytes(b'video')
            return {}

    with patch('video_processor.downloader.YoutubeDL', FakeYoutubeDL):
        download_instagram_reel('https://www.instagram.com/reel/DbdjZO6RYXw/', tmp_path, cookie_file=cookie_file)

    assert FakeYoutubeDL.options['cookiefile'] == str(cookie_file)
