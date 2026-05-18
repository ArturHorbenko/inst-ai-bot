# Light indexing; visual analysis deferred to run time

Indexing a video produces only: content hash, transcript (Whisper), source metadata, and a reference to the video file. Scene detection, OCR, and AI captioning are not run at index time. Every prompt run sends the video file + transcript to a multimodal model directly.

The alternative (heavy index: pre-extract OCR, captions, scene list into structured JSON; prompt runs are text-only completions against that JSON) is cheaper per iteration but lossy — the pre-extracted text is a degraded approximation of what a modern multimodal model can read directly from frames. Since the primary use case is iterating on prompts many times against the same video, and modern multimodal models (Gemini 2.5, GPT-5, Claude 4) handle visual analysis well from raw video, the full-fidelity approach is the right default. Heavy pre-extraction can be added as an optional artifact field later for use cases that specifically need it.
