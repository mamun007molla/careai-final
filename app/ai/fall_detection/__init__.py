"""Fall detection — wraps the multimodal pipeline.

Public function:
    run_fall_detection(video_bytes, filename) -> dict

Returns a dict with:
    fall_detected, confidence, mode, has_audio, segments,
    output_video_bytes (Optional[bytes])
"""
from app.ai.fall_detection.detector import run_fall_detection

__all__ = ["run_fall_detection"]
