import os
import subprocess
import logging
from groq import Groq

logger = logging.getLogger(__name__)

def extract_transcription(path, api_key=None):
    """Extract audio transcription from video using Groq Whisper API"""
    try:
        audio_path = "./temp/audio.wav"
        os.makedirs("./temp", exist_ok=True)

        logger.info("Starting audio extraction...")
        command = f"ffmpeg -y -i {path} -ab 160k -ac 2 -ar 16000 -vn {audio_path}"
        subprocess.call(command, shell=True)

        logger.info("Transcribing audio via Groq Whisper API...")
        client = Groq(api_key=api_key)

        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments = response.segments or []
        result = [
            {
                "start": round(seg["start"], 1),
                "end": round(seg["end"], 1),
                "text": seg["text"],
            }
            for seg in segments
        ]
        logger.info(f"Transcription result: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in extract_transcription: {str(e)}")
        raise
