import os
from dataclasses import dataclass
from dotenv import load_dotenv


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    MONGODB_URI: str
    MONGODB_DB: str
    VIDEO_DIR: str = "videos"
    OPENAI_API_KEY: str = None
    GEMINI_API_KEY: str = None
    GROQ_API_KEY: str = None
    TWELVE_LABS_API_KEY: str = None
    TWELVE_LABS_INDEX_NAME: str = "default-index"
    TWELVE_LABS_INDEX_ID: str = None
    INDEXING_TIMEOUT: int = 1800
    INDEXING_POLL_INTERVAL: int = 10
    SUPPORTED_VIDEO_FORMATS: str = "mp4,mov,avi,mkv,webm,m4v"
    INSTAGRAM_COOKIES_FILE: str = "secrets/instagram-cookies.txt"
    ANALYTICS_DASHBOARD_URL: str = None
    ANALYTICS_DASHBOARD_API_KEY: str = None


def get_config():
    load_dotenv(override=True)

    return Config(
        MONGODB_URI=os.environ.get("MONGODB_URI", "mongodb://localhost:27017/"),
        MONGODB_DB=os.environ.get("MONGODB_DB", "creator-kb"),
        VIDEO_DIR=os.environ.get("VIDEO_DIR", "videos"),
        OPENAI_API_KEY=os.environ.get("OPENAI_API_KEY"),
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY"),
        GROQ_API_KEY=os.environ.get("GROQ_API_KEY"),
        TWELVE_LABS_API_KEY=os.environ.get("TWELVE_LABS_API_KEY"),
        TWELVE_LABS_INDEX_NAME=os.environ.get("TWELVE_LABS_INDEX_NAME", "default-index"),
        TWELVE_LABS_INDEX_ID=os.environ.get("TWELVE_LABS_INDEX_ID"),
        INDEXING_TIMEOUT=int(os.environ.get("INDEXING_TIMEOUT", "1800")),
        INDEXING_POLL_INTERVAL=int(os.environ.get("INDEXING_POLL_INTERVAL", "10")),
        SUPPORTED_VIDEO_FORMATS=os.environ.get("SUPPORTED_VIDEO_FORMATS", "mp4,mov,avi,mkv,webm,m4v"),
        INSTAGRAM_COOKIES_FILE=os.environ.get("INSTAGRAM_COOKIES_FILE", "secrets/instagram-cookies.txt"),
        ANALYTICS_DASHBOARD_URL=os.environ.get("ANALYTICS_DASHBOARD_URL"),
        ANALYTICS_DASHBOARD_API_KEY=os.environ.get("ANALYTICS_DASHBOARD_API_KEY"),
    )


def validate_video_format(video_path: str, config: Config) -> bool:
    """
    Validate if video format is supported based on file extension.

    Args:
        video_path: Path to video file
        config: Configuration object

    Returns:
        True if format is supported, False otherwise
    """
    if not video_path:
        return False

    # Get file extension
    file_extension = video_path.lower().split('.')[-1]

    # Check if extension is in supported formats
    supported_formats = [fmt.strip().lower() for fmt in config.SUPPORTED_VIDEO_FORMATS.split(',')]

    return file_extension in supported_formats
