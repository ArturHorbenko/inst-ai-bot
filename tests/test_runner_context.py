from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_processor.runner import build_artifact_context, run_prompt


def test_build_artifact_context_includes_available_timestamped_transcript_source_and_comments():
    context = build_artifact_context({
        'transcript': {
            'text': 'Fallback transcript.',
            'segments': [
                {'start': 0.0, 'end': 1.4, 'text': 'First spoken line.'},
                {'start': 1.4, 'end': 3.0, 'text': 'Second spoken line.'},
            ],
        },
        'sources': [{
            'type': 'instagram_graph_api',
            'url': 'https://www.instagram.com/reel/example/',
            'metadata': {
                'caption': 'Caption text',
                'comments': ['Useful comment', 'Another comment'],
            },
        }],
    })

    assert '[0.0s–1.4s] First spoken line.' in context
    assert '[1.4s–3.0s] Second spoken line.' in context
    assert 'Caption text' in context
    assert 'Useful comment' in context
    assert 'Another comment' in context


def test_build_artifact_context_uses_plain_transcript_when_segments_are_unavailable():
    context = build_artifact_context({
        'transcript': {'text': 'Audio-only transcript.'},
        'sources': [],
    })

    assert 'Audio-only transcript.' in context


def test_build_artifact_context_is_empty_when_no_context_is_available():
    assert build_artifact_context({}) == ''


class ContextArtifactStore:
    def get_by_hash(self, content_hash):
        return {
            'content_hash': content_hash,
            'video_file_ref': 'videos/example.mp4',
            'gemini_file_ref': None,
            'transcript': {'segments': [{'start': 0.0, 'end': 1.0, 'text': 'Spoken context.'}]},
            'sources': [{'metadata': {'caption': 'Source context', 'comments': ['Comment context']}}],
        }

    def update_gemini_ref(self, *_args):
        pass


class ContextRunsStore:
    def insert(self, run):
        return run


def test_run_prompt_supplies_available_artifact_context_to_gemini_and_persists_it():
    call_gemini = Mock(return_value=('{}', None))
    with patch('video_processor.runner.gemini_module.call_gemini', call_gemini):
        run = run_prompt(
            'sha256:example', 'Analyze this video.', 'google/gemini-2.5-pro', None,
            SimpleNamespace(GEMINI_API_KEY='key'), ContextArtifactStore(), ContextRunsStore(),
        )

    effective_prompt = call_gemini.call_args.kwargs['prompt']
    assert 'Analyze this video.' in effective_prompt
    assert 'Spoken context.' in effective_prompt
    assert 'Source context' in effective_prompt
    assert 'Comment context' in effective_prompt
    assert run['prompt'] == effective_prompt
