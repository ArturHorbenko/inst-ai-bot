import os
import subprocess

def extract_transcription(path):
    """Extract audio transcription from video using Whisper"""
    try:
        try:
            import whisper
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Structured transcription requires `openai-whisper`. "
                "Install with: pip install \"setuptools<81\" && "
                "pip install --no-build-isolation openai-whisper==20240930"
            ) from exc

        output = "./temp/audio.wav"
        # Create temp directory if it doesn't exist
        os.makedirs("./temp", exist_ok=True)
        
        print("Starting audio extraction...")
        # Add the -y flag to force overwrite without prompting
        command = f"ffmpeg -y -i {path} -ab 160k -ac 2 -ar 44100 -vn {output}"
        subprocess.call(command, shell=True)

        print("Loading Whisper model...")
        model = whisper.load_model("large")

        print("Transcribing audio...")
        result = model.transcribe(output)

        new_list = list(
            map(
                lambda x: {
                    "start": round(x.get("start"), 1),
                    "end": round(x.get("end"), 1),
                    "text": x.get("text"),
                },
                result.get("segments"),
            )
        )
        print("Transcription result:", new_list)
        return new_list
    except Exception as e:
        print(f"Error in extract_transcription: {str(e)}")
        raise 
