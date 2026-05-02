"""Whisper-based voice transcription for chat messages.

Used when:
  - User taps the 🎤 record button in the chat UI (audio webm/mp4 blob)
  - An inbound email arrives with an audio/* attachment (WhatsApp voice forward)

Returns the plain transcript text or '' on any failure (transcription is
optional — if it fails, the audio file is still saved + visible in chat
history so the user can listen to it manually).
"""
import os
import openai


# Whisper accepts: mp3, mp4, mpeg, mpga, m4a, wav, webm (max 25MB per file)
SUPPORTED_AUDIO_EXTS = {'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm', 'oga', 'ogg'}
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Whisper API limit


def transcribe(file_path: str, language_hint: str = '') -> str:
    """Transcribe an audio file using OpenAI Whisper.

    language_hint: ISO-639-1 (e.g. 'en', 'ms', 'zh'). Empty = auto-detect.
    Returns the transcript or '' on any error.
    """
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return ''
    if not file_path or not os.path.isfile(file_path):
        return ''
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return ''
    if size == 0 or size > MAX_AUDIO_BYTES:
        return ''
    ext = file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ''
    if ext not in SUPPORTED_AUDIO_EXTS:
        return ''

    client = openai.OpenAI(api_key=api_key)
    try:
        with open(file_path, 'rb') as f:
            kwargs = {'model': 'whisper-1', 'file': f, 'response_format': 'text'}
            if language_hint:
                kwargs['language'] = language_hint
            result = client.audio.transcriptions.create(**kwargs)
        # response_format='text' returns the bare transcript string
        text = result if isinstance(result, str) else getattr(result, 'text', '')
        return (text or '').strip()
    except Exception:
        return ''


def is_audio(filename_or_mime: str) -> bool:
    """Quick check — does this look like an audio file we can transcribe?"""
    if not filename_or_mime:
        return False
    s = filename_or_mime.lower()
    if s.startswith('audio/'):
        return True
    ext = s.rsplit('.', 1)[-1] if '.' in s else s
    return ext in SUPPORTED_AUDIO_EXTS
