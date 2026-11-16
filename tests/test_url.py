"""Tests for YouTube URL parsing and normalization."""

import unittest

from twitch_musicbot.player import extract_youtube_video_id


class TestYouTubeUrlParsing(unittest.TestCase):
    def test_standard_desktop_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_mobile_url(self):
        url = "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_music_url(self):
        url = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_short_youtu_be_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ?si=abcdef123"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_embed_url(self):
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_query_params_arbitrary_order(self):
        url = "https://www.youtube.com/watch?feature=shared&app=desktop&v=dQw4w9WgXcQ&t=42s"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_without_protocol(self):
        url = "youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extract_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_invalid_urls(self):
        self.assertIsNone(extract_youtube_video_id("https://google.com/watch?v=dQw4w9WgXcQ"))
        self.assertIsNone(extract_youtube_video_id("not a url at all"))
        self.assertIsNone(extract_youtube_video_id("https://youtube.com/watch?v=short"))
        self.assertIsNone(extract_youtube_video_id(""))
        self.assertIsNone(extract_youtube_video_id("https://youtube.com/"))


if __name__ == "__main__":
    unittest.main()
