# test.py

import warnings
from pathlib import Path
from datetime import datetime
import os

# Defer heavy scientific / ML imports into functions to keep module import lightweight

# MediaPipe setup is deferred into `extract_per_frame` to avoid heavy imports at module import time.

# Landmark indices
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28

# Helpers (winsor, ema, etc.)
def _mid(a, b): return (a + b) / 2.0
def _norm(v, eps=1e-8): ##Normalizing function
    n = np.linalg.norm(v)
    return v / (n + eps), n
def _angle_to_vertical_deg(v):
    vy = np.array([0.0, -1.0], dtype=np.float32) ##Vertical AXIS vector
    vu, _ = _norm(v.astype(np.float32)) 
    c = np.clip(float(vu @ vy), -1.0, 1.0)
    return float(np.degrees(np.arccos(c)))
def _angle3(a, b, c):
    va = a - b; vc = c - b
    va_u, _ = _norm(va); vc_u, _ = _norm(vc)
    cval = np.clip(float(va_u @ vc_u), -1.0, 1.0)
    return float(np.degrees(np.arccos(cval)))


def _ema(x, α=0.25): ##Moving Averages
    y = np.empty_like(x, dtype=np.float32)
    if not len(x): return y
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = α * x[i] + (1 - α) * y[i - 1]
    return y



def _winsor_mad(x, k=5.0): ###Winsorizing function & outlier removal
    x = np.asarray(x, np.float32); m = np.isfinite(x)
    if m.sum() < 3: return x
    med = np.median(x[m]); mad = np.median(np.abs(x[m] - med)) + 1e-6
    lo, hi = med - k * 1.4826 * mad, med + k * 1.4826 * mad
    y = x.copy(); y[m & (y < lo)] = lo; y[m & (y > hi)] = hi
    return y


def _interp_nans(x):
    x = np.asarray(x, np.float32); idx = np.arange(len(x)); m = np.isfinite(x)
    if not m.any(): return np.zeros_like(x)
    x[~m] = np.interp(idx[~m], idx[m], x[m]); return x


def _bbox(pts):
    xs, ys = pts[:,0], pts[:,1]; return float(xs.min()),float(ys.min()),float(xs.max()),float(ys.max())

# Per-frame extraction
def extract_per_frame(path, fps=30.0, n=90, α=0.25):
    # Import heavy dependencies here to keep top-level import fast when this file
    # is loaded as a module by other scripts.
    import cv2
    import mediapipe as mp
    import numpy as _np
    import pandas as _pd
    import urllib.request

    # Make numpy/pandas available to helper functions that reference `np`/`pd`.
    globals()['np'] = _np
    globals()['pd'] = _pd

    # Setup MediaPipe pose API (fall back to Tasks API if required)
    try:
        mp_pose = mp.solutions.pose
    except Exception:
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.vision.core import image as image_lib
        MPImage = image_lib.Image
        MPImageFormat = image_lib.ImageFormat
        from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions
        from mediapipe.tasks.python.core import base_options as base_options_lib

        def _ensure_pose_model(model_path="pose_landmarker.task"):
            if not os.path.exists(model_path):
                url = (
                    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
                    "pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task"
                )
                urllib.request.urlretrieve(url, model_path)
            return model_path

        class _PoseCompat:
            def __init__(self, static_image_mode=False, model_complexity=1):
                model_file = _ensure_pose_model()
                base_options = base_options_lib.BaseOptions(model_asset_path=model_file)
                options = vision.PoseLandmarkerOptions(
                    base_options=base_options, running_mode=vision.RunningMode.IMAGE
                )
                self._landmarker = vision.PoseLandmarker.create_from_options(options)

            def process(self, rgb_image):
                mp_img = MPImage(MPImageFormat.SRGB, rgb_image)
                result = self._landmarker.detect(mp_img)
                class _Res:
                    pass
                res = _Res()
                if not result.pose_landmarks:
                    res.pose_landmarks = None
                else:
                    landmarks = []
                    for lm in result.pose_landmarks[0]:
                        obj = type("LM", (), {})()
                        obj.x = lm.x
                        obj.y = lm.y
                        obj.z = getattr(lm, "z", 0.0)
                        landmarks.append(obj)
                    res.pose_landmarks = type("LL", (), {"landmark": landmarks})
                return res

            def close(self):
                try:
                    self._landmarker.close()
                except Exception:
                    pass

        mp_pose = type("compat", (), {"Pose": _PoseCompat})

    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or n)
    idxs = np.linspace(0, total - 1, n, dtype=int)
    pose = mp_pose.Pose(static_image_mode=False, model_complexity=1)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)

    ts, A, AR, KA, HH, COM = [], [], [], [], [], []
    for i, f in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        ts.append(i / fps)
        if not ok:
            A.append(np.nan); AR.append(np.nan); KA.append(np.nan)
            HH.append(np.nan); COM.append([np.nan,np.nan]); continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = pose.process(rgb)
        if not res.pose_landmarks:
            A.append(np.nan); AR.append(np.nan); KA.append(np.nan)
            HH.append(np.nan); COM.append([np.nan,np.nan]); continue

        pts = np.array([[lm.x*W, lm.y*H] for lm in res.pose_landmarks.landmark], np.float32)
        head = pts[NOSE]; lsh, rsh = pts[LEFT_SHOULDER], pts[RIGHT_SHOULDER]
        lhp, rhp = pts[LEFT_HIP], pts[RIGHT_HIP]
        lkn, rkn = pts[LEFT_KNEE], pts[RIGHT_KNEE]
        lan, ran = pts[LEFT_ANKLE], pts[RIGHT_ANKLE]

        scale = np.median([np.linalg.norm(rsh-lsh), np.linalg.norm(rhp-lhp),1e-3])
        mid_sh = _mid(lsh, rsh); mid_hp = _mid(lhp, rhp)
        A.append(_angle_to_vertical_deg(mid_hp-mid_sh))

        xmin,ymin,xmax,ymax = _bbox(pts)
        AR.append((ymax-ymin)/(xmax-xmin+1e-6))

        k1 = _angle3(lhp, lkn, lan); k2 = _angle3(rhp, rkn, ran)
        KA.append(np.nanmean([k1,k2]))

        HH.append((head[1]-mid_hp[1])/(scale+1e-6))

        major = np.vstack([lsh,rsh,lhp,rhp,lkn,rkn,lan,ran])
        COM.append(np.nanmean(major,axis=0))

    cap.release(); pose.close()
    ts = np.asarray(ts,np.float32)
    A = _interp_nans(_winsor_mad(np.asarray(A))); AR = _interp_nans(_winsor_mad(np.asarray(AR)))
    KA = _interp_nans(_winsor_mad(np.asarray(KA))); HH = _interp_nans(_winsor_mad(np.asarray(HH)))
    COM = np.stack(COM)
    for d in (0,1): COM[:,d]=_interp_nans(_winsor_mad(COM[:,d]))

    A = _ema(A,α); AR = _ema(AR,α); KA = _ema(KA,α); HH = _ema(HH,α)
    COM = np.stack([_ema(COM[:,0],α), _ema(COM[:,1],α)], axis=1)

    dt = np.gradient(ts)+1e-6
    dA = np.gradient(A)/dt
    dC = (np.vstack([np.gradient(COM[:,0]),np.gradient(COM[:,1])]).T)/dt[:,None]
    v = np.linalg.norm(dC,axis=1)
    a = np.gradient(v)/dt; j = np.gradient(a)/dt
    angmag = np.abs(dA); logup = np.log1p(np.abs(A))

    return pd.DataFrame({
        "timestamp":ts,
        "torso_angle_deg":A,
        "angular_velocity_deg_s":dA,
        "angular_velocity":angmag,
        "aspect_ratio":AR,
        "knee_angle":KA,
        "head_hip_gap_norm":HH,
        "com_speed":v,
        "com_acc":a,
        "com_jerk":j,
        "log_upper_body_angle":logup
    })


def main():
    FALL_DIR = Path(r"E:\Fall-Detection research latest\Fall-Detection-Vdo-master\Video Files\fall\Second Counting Videos Fall\all_3_second_videos_fall")
    NOTFALL_DIR = Path(r"E:\Fall-Detection research latest\Fall-Detection-Vdo-master\Video Files\not_fall\Second Counting Videos\all 3 second videos not fall")
    today = datetime.now().strftime("%Y-%m-%d")
    outbase = Path(f"outputs_{today}")
    pf_fall = outbase/f"{today}_perframe_fall"; pf_nf = outbase/f"{today}_perframe_not_fall"
    outbase.mkdir(exist_ok=True,parents=True); pf_fall.mkdir(exist_ok=True,parents=True); pf_nf.mkdir(exist_ok=True,parents=True)

    # Discover all video files
    vids = [(p,1) for p in FALL_DIR.rglob("*") if p.suffix.lower() in [".mp4",".avi"]] \
         + [(p,0) for p in NOTFALL_DIR.rglob("*") if p.suffix.lower() in [".mp4",".avi"]]
    
    print(f"Total videos found: {len(vids)}")
    
    # Check which videos already have CSV files
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    for i, (p, label) in enumerate(vids):
        # Determine output CSV path
        output_csv = (pf_fall if label else pf_nf) / f"{p.stem}.csv"
        
        # Check if CSV already exists
        if output_csv.exists():
            processed_count += 1
            print(f"[{i+1}/{len(vids)}] ALREADY PROCESSED: {p.name} -> {output_csv.name}")
            continue
            
        # Process the video
        try:
            print(f"[{i+1}/{len(vids)}] PROCESSING: {p.name} -> {output_csv.name}")
            df_pf = extract_per_frame(p)
            df_pf.to_csv(output_csv, index=False)
            processed_count += 1
            
        except Exception as e:
            error_count += 1
            warnings.warn(f"{p.name} skipped: {e}")
            print(f"[{i+1}/{len(vids)}] ERROR: {p.name} - {e}")

    print(f"\n=== PROCESSING SUMMARY ===")
    print(f"Total videos: {len(vids)}")
    print(f"Already processed (skipped): {skipped_count}")
    print(f"Successfully processed: {processed_count}")
    print(f"Errors: {error_count}")
    print(f"Per-frame CSVs saved in: {pf_fall} and {pf_nf}")

if __name__=="__main__":
    main()