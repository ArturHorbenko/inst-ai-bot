import os
from dataclasses import dataclass
from dotenv import load_dotenv


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    VIDEO_PATH: str
    VIDEO_ID: str
    IMAGE_DIR: str
    MONGODB_URI: str
    MONGODB_DB: str
    OPENAI_API_KEY: str = None
    GEMINI_API_KEY: str = None
    GROQ_API_KEY: str = None
    TWELVE_LABS_API_KEY: str = None
    TWELVE_LABS_INDEX_NAME: str = "default-index"
    TWELVE_LABS_INDEX_ID: str = None
    INDEXING_TIMEOUT: int = 1800  # 30 minutes
    INDEXING_POLL_INTERVAL: int = 10  # 10 seconds
    SUPPORTED_VIDEO_FORMATS: str = "mp4,mov,avi,mkv,webm,m4v"
    ENABLE_MULTIMODAL_ANALYSIS: bool = True

def get_config():
    """Load configuration from environment variables or defaults"""

    load_dotenv(override=True)

    # You can expand this to load from a config file or environment variables
    config = Config(
        VIDEO_PATH=os.environ.get("VIDEO_PATH", "./reels/3505607525892794154_5599290503.mp4"),
        VIDEO_ID=os.environ.get("VIDEO_ID", "3505607525892794154_5599290503.mp4"),
        IMAGE_DIR=os.environ.get("IMAGE_DIR", "./scenes"),
        MONGODB_URI=os.environ.get("MONGODB_URI", "mongodb://localhost:27017/"),
        MONGODB_DB=os.environ.get("MONGODB_DB", "creator-kb"),
        OPENAI_API_KEY=os.environ.get("OPENAI_API_KEY"),
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY"),
        GROQ_API_KEY=os.environ.get("GROQ_API_KEY"),
        TWELVE_LABS_API_KEY=os.environ.get("TWELVE_LABS_API_KEY"),
        TWELVE_LABS_INDEX_NAME=os.environ.get("TWELVE_LABS_INDEX_NAME", "default-index"),
        TWELVE_LABS_INDEX_ID=os.environ.get("TWELVE_LABS_INDEX_ID"),
        INDEXING_TIMEOUT=int(os.environ.get("INDEXING_TIMEOUT", "1800")),
        INDEXING_POLL_INTERVAL=int(os.environ.get("INDEXING_POLL_INTERVAL", "10")),
        SUPPORTED_VIDEO_FORMATS=os.environ.get("SUPPORTED_VIDEO_FORMATS", "mp4,mov,avi,mkv,webm,m4v"),
        ENABLE_MULTIMODAL_ANALYSIS=_parse_bool(os.environ.get("ENABLE_MULTIMODAL_ANALYSIS"), True),
    )

    return config


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
