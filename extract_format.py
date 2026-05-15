"""
Extract reusable video format templates from tutorial videos.

Usage:
    python extract_format.py /path/to/video.mp4
    python extract_format.py /path/to/dir/
    python extract_format.py ~/Downloads/vid1.mp4 ~/Downloads/vid2.mp4
    python extract_format.py /path/to/dir/ -o formats/ --json
"""

import argparse
import glob
import json
import logging
import os
import sys
import time

from video_processor.config import get_config, validate_video_format
from video_processor.transcription import extract_transcription
from video_processor.multimodal import analyze_video_format_extraction

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def find_videos(paths: list[str]) -> list[str]:
    """Resolve a mix of files and directories into a flat list of video paths."""
    videos = []
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isdir(path):
            for ext in VIDEO_EXTENSIONS:
                videos.extend(glob.glob(os.path.join(path, f"*{ext}")))
        elif os.path.isfile(path):
            videos.append(path)
        else:
            print(f"Warning: skipping {path} (not found)")
    return sorted(set(videos))


def process_one(video_path: str, config, output_dir: str, write_json: bool) -> bool:
    """Process a single video. Returns True on success."""
    filename = os.path.basename(video_path)
    file_size = os.path.getsize(video_path) / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f"Processing: {filename} ({file_size:.1f} MB)")
    print(f"{'='*60}")

    if not validate_video_format(video_path, config):
        print(f"  Skipping: unsupported format")
        return False

    # Step 1: Whisper transcription
    transcript = None
    try:
        print("  Transcribing with Whisper...")
        transcript = extract_transcription(video_path, api_key=config.GROQ_API_KEY)
        print(f"  Transcription complete: {len(transcript)} segments")
    except Exception as e:
        print(f"  Warning: transcription failed ({e}), proceeding without it")

    # Step 2: Gemini format extraction
    print("  Extracting format with Gemini...")
    start = time.time()
    try:
        result = analyze_video_format_extraction(config.GEMINI_API_KEY, video_path, transcript)
    except Exception as e:
        print(f"  Error: {e}")
        return False

    elapsed = time.time() - start

    if result["status"] != "completed":
        print(f"  Error: {result.get('error', result)}")
        return False

    format_name = result["summary"]["format_name"]
    print(f"  Format extracted in {elapsed:.1f}s: {format_name}")

    # Step 3: Write output
    os.makedirs(output_dir, exist_ok=True)
    slug = format_name.lower().replace(" ", "-").replace("/", "-").replace("'", "").replace('"', "")
    md_path = os.path.join(output_dir, f"{slug}.md")

    with open(md_path, "w") as f:
        f.write(result["markdown"])
    print(f"  Wrote: {md_path}")

    if write_json:
        json_path = os.path.join(output_dir, f"{slug}.json")
        with open(json_path, "w") as f:
            json.dump(result["summary"], f, indent=2)
        print(f"  Wrote: {json_path}")

    return True


def main():
    parser = argparse.ArgumentParser(description="Extract video format templates from tutorial videos")
    parser.add_argument("paths", nargs="+", help="Video files or directories containing videos")
    parser.add_argument("-o", "--output", help="Output directory for .md files", default="formats/")
    parser.add_argument("--json", action="store_true", help="Also write structured JSON alongside markdown")
    args = parser.parse_args()

    videos = find_videos(args.paths)
    if not videos:
        print("No video files found.")
        sys.exit(1)

    config = get_config()
    print(f"Found {len(videos)} video(s) to process")
    for v in videos:
        print(f"  - {os.path.basename(v)}")

    succeeded = 0
    failed = 0
    for i, video_path in enumerate(videos, 1):
        print(f"\n[{i}/{len(videos)}]", end="")
        if process_one(video_path, config, args.output, args.json):
            succeeded += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"Done: {succeeded} succeeded, {failed} failed out of {len(videos)} total")


if __name__ == "__main__":
    main()
