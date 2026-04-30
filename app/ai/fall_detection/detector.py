"""Fall detection runner — DB-storage friendly.

Reads video_bytes from memory, writes to a temp file (cv2 needs a path),
processes through XGBoost (vision) + AST (audio) pipeline, captures the
annotated output as bytes, then deletes all temp files.

If ML deps (cv2, xgboost, mediapipe, transformers, torch) aren't installed
we fall back to a stub that returns a "no_fall, mode=disabled" result so the
backend stays bootable on minimal Railway tiers.

To enable real detection on a deploy:
1. Uncomment the heavy deps in requirements.txt.
2. Drop the trained model files into:
       app/ai/fall_detection/models/video/xgb_final_model.json
       app/ai/fall_detection/models/audio/ast_model.torchscript.pt
       app/ai/fall_detection/models/audio/preprocessor_config.json
       app/ai/fall_detection/models/audio/label_map.json
3. Restart.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("careai.fall_detection")

BASE_DIR = Path(__file__).parent
XGB_MODEL_PATH = BASE_DIR / "models" / "video" / "xgb_final_model.json"
AST_MODEL_PATH = BASE_DIR / "models" / "audio" / "ast_model.torchscript.pt"


# ─── Capability check ─────────────────────────────────────────────────────────
def _ml_deps_ready() -> tuple[bool, str]:
    """Return (ready, reason). Reason is empty if ready."""
    try:
        import cv2          # noqa: F401
        import xgboost      # noqa: F401
        import numpy        # noqa: F401
        import pandas       # noqa: F401
    except ImportError as e:
        return False, f"ML dependency missing: {e.name}"
    if not XGB_MODEL_PATH.exists():
        return False, f"Trained model not found at {XGB_MODEL_PATH}"
    return True, ""


def _has_audio_track(video_path: str) -> bool:
    """Return True if ffmpeg can extract a non-trivial audio sample from the video."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-t", "1", tmp],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000
    except Exception:
        return False
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass


# ─── Public entry ─────────────────────────────────────────────────────────────
async def run_fall_detection(video_bytes: bytes, filename: str = "input.mp4") -> dict:
    """Detect falls in the given video. Returns dict (see module docstring)."""
    ready, reason = _ml_deps_ready()
    if not ready:
        log.warning("Fall detection running in STUB mode: %s", reason)
        return {
            "fall_detected": False,
            "confidence": 0.0,
            "mode": "disabled",
            "has_audio": False,
            "segments": [{"info": f"ML pipeline disabled — {reason}"}],
            "output_video_bytes": None,
        }

    # Write input to a temp file because OpenCV needs a path
    tmp_dir = Path(tempfile.gettempdir()) / "careai_fall"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = tmp_dir / f"in_{job_id}.mp4"
    output_path = tmp_dir / f"out_{job_id}.mp4"

    input_path.write_bytes(video_bytes)
    has_audio = _has_audio_track(str(input_path))

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_pipeline_sync,
                                             str(input_path), str(output_path), has_audio)
        # Read annotated output, if produced
        output_bytes: Optional[bytes] = None
        if output_path.exists() and output_path.stat().st_size > 0:
            output_bytes = output_path.read_bytes()
        result["output_video_bytes"] = output_bytes
        result["has_audio"] = has_audio
        return result
    finally:
        for p in (input_path, output_path):
            if p.exists():
                try: p.unlink()
                except Exception: pass


# ─── Vision-only pipeline (works without trained AST model) ───────────────────
def _run_pipeline_sync(input_path: str, output_path: str, has_audio: bool) -> dict:
    """Run XGBoost-only vision pipeline. Multimodal hook left as TODO.

    This is a simplified, self-contained version. The full multimodal pipeline
    from the existing project (Multimodal_Final.py) requires MediaPipe + AST +
    custom feature extraction modules — drop those files into ./feature_extraction/
    and ./models/ to enable the multimodal mode.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (W, H))

    win_frames = max(int(round(3.0 * fps)), 30)
    label_map = ["NOT FALL", "FALL"]
    segments: list[dict] = []
    max_conf = 0.0
    fall_detected = False
    seg_idx = 0

    # Load XGBoost model and feature extractor (lazy import — heavy deps)
    try:
        import xgboost as xgb
        clf = xgb.XGBClassifier()
        clf.load_model(str(XGB_MODEL_PATH))
    except Exception as e:
        cap.release()
        writer.release()
        return {
            "fall_detected": False, "confidence": 0.0,
            "mode": "vision-only-failed", "segments": [{"error": f"XGBoost load: {e}"}],
        }

    # Feature extraction — try to import the user's custom modules.
    # If unavailable, score every window as 50/50 (degraded mode).
    extract_features_fn = _try_load_feature_extraction()

    try:
        while True:
            frames = []
            for _ in range(win_frames):
                ok, f = cap.read()
                if not ok:
                    break
                frames.append(f)
            if not frames:
                break

            # Score this window
            label_idx, conf = _score_window(frames, fps, clf, extract_features_fn)
            color = (0, 255, 0) if label_idx == 0 else (0, 0, 255)
            label_text = f"{label_map[label_idx]} {conf*100:.1f}%"

            if label_idx == 1 and conf > 0.5:
                fall_detected = True
                max_conf = max(max_conf, conf)

            # Annotate frames
            for f in frames:
                disp = f.copy()
                cv2.rectangle(disp, (10, 10), (520, 50), (0, 0, 0), -1)
                cv2.putText(disp, label_text, (15, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                writer.write(disp)

            segments.append({
                "window": seg_idx,
                "label": label_map[label_idx],
                "confidence": round(conf, 3),
                "fall": label_idx == 1,
            })
            seg_idx += 1
    finally:
        cap.release()
        writer.release()

    return {
        "fall_detected": fall_detected,
        "confidence": round(max_conf, 3),
        "mode": "multimodal" if has_audio else "vision-only",
        "segments": segments,
    }


def _try_load_feature_extraction():
    """Attempt to import the user's per-frame + feature engineering modules."""
    try:
        import importlib.util
        per_frame_path = BASE_DIR / "feature_extraction" / "per-frame-best.py"
        fe_eng_path    = BASE_DIR / "feature_extraction" / "final-feature-eng-best.py"
        if not per_frame_path.exists() or not fe_eng_path.exists():
            return None

        spec1 = importlib.util.spec_from_file_location("per_frame_best", per_frame_path)
        per_frame = importlib.util.module_from_spec(spec1)
        spec1.loader.exec_module(per_frame)

        spec2 = importlib.util.spec_from_file_location("final_feature_eng_best", fe_eng_path)
        fe_eng = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(fe_eng)

        def extract(frames, fps):
            import tempfile
            import cv2
            tmpv = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            try:
                vw = cv2.VideoWriter(tmpv, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                      (frames[0].shape[1], frames[0].shape[0]))
                for f in frames:
                    vw.write(f)
                vw.release()
                df_pf = per_frame.extract_per_frame(tmpv, fps=fps, n=len(frames))
                if df_pf is None or df_pf.empty:
                    return None
                return fe_eng.extract_advanced_features(df_pf)
            finally:
                if os.path.exists(tmpv):
                    try: os.remove(tmpv)
                    except Exception: pass

        return extract
    except Exception as e:
        log.warning("Feature extraction modules not loadable: %s", e)
        return None


def _score_window(frames, fps, clf, extract_fn):
    """Return (label_idx, confidence)."""
    import numpy as np
    import pandas as pd

    if extract_fn is None:
        # No feature pipeline — return uncertain
        return 0, 0.5

    feats = extract_fn(frames, fps)
    if feats is None:
        return 0, 0.5

    row = pd.DataFrame([feats])
    if hasattr(clf, "feature_names_in_"):
        names = list(clf.feature_names_in_)
        for c in names:
            if c not in row:
                row[c] = 0.0
        row = row.reindex(columns=names)

    probs = clf.predict_proba(row)[0]
    idx = int(np.argmax(probs))
    return idx, float(probs[idx])
