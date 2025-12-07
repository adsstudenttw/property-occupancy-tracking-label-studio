import os
import requests
import subprocess
import yaml
from pathlib import Path

CONFIG = yaml.safe_load(open("config.yml"))

PROJECT_ID = CONFIG["label_studio"]["project_id"]

EXPORT_DIR = Path(CONFIG["exports"]["export_dir"])
SPLIT_DIR = Path(CONFIG["exports"]["split_dir"])
OUTPUT_DIR = Path(CONFIG["mot"]["output_dir"])
VIDEO_DIR = Path(CONFIG["videos"]["directory"])

TARGET_FPS = CONFIG["video_processing"]["target_fps"]
USE_ORIGINAL_RES = CONFIG["video_processing"]["use_original_resolution"]
DEFAULT_WIDTH = CONFIG["video_processing"]["default_width"]
DEFAULT_HEIGHT = CONFIG["video_processing"]["default_height"]


LS_URL = os.getenv("LABEL_STUDIO_URL")
API_KEY = os.getenv("LABEL_STUDIO_API_KEY")


# Helper: auto-detect resolution
def ffprobe_info(video_path):
    import json

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]
    out = subprocess.check_output(cmd).decode("utf-8")
    data = json.loads(out)

    stream = next(s for s in data["streams"] if s["codec_type"] == "video")

    width = int(stream["width"])
    height = int(stream["height"])

    r_num, r_den = stream["r_frame_rate"].split("/")
    fps = float(r_num) / float(r_den)

    duration = float(data["format"]["duration"])
    frame_count = int(duration * fps)

    return width, height, fps, duration, frame_count


# Fetch COCO export from LS
def fetch_coco_export():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    url = f"{LS_URL}/api/projects/{PROJECT_ID}/export?exportType=coco"
    headers = {"Authorization": f"Token {API_KEY}"}

    r = requests.get(url, headers=headers)
    r.raise_for_status()

    out = EXPORT_DIR / f"project_{PROJECT_ID}_coco.json"
    out.write_bytes(r.content)
    print("COCO export saved:", out)

    return out


# Split per video
def split_coco(coco_path):
    subprocess.run(
        [
            "python",
            "scripts/split_coco_by_video.py",
            "--coco",
            str(coco_path),
            "--outdir",
            str(SPLIT_DIR),
        ],
        check=True,
    )
    return list(SPLIT_DIR.glob("*_coco.json"))


# MOT conversion
def convert_to_mot(coco_path, seqname, width, height, fps):
    subprocess.run(
        [
            "python",
            "scripts/coco_to_mot_simple.py",
            "--coco",
            str(coco_path),
            "--outdir",
            str(OUTPUT_DIR),
            "--seqname",
            seqname,
            "--width",
            str(width),
            "--height",
            str(height),
            "--fps",
            str(fps),
        ],
        check=True,
    )


# Extract frames at TARGET_FPS
def extract_frames(video_path, seqname, target_fps):
    """
    Extract frames at a fixed temporal sampling rate (fps frames per second).

    For Option A we set fps=1, so you only label one frame per second and
    the MOT sequence is also 1 FPS.
    """
    img_dir = OUTPUT_DIR / seqname / "img1"
    img_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            f"fps={target_fps}",
            "-qscale:v",
            "2",
            str(img_dir / "%06d.jpg"),
        ],
        check=True,
    )


# MAIN WORKFLOW
if __name__ == "__main__":
    coco = fetch_coco_export()
    split_exports = split_coco(coco)

    videos = list(Path(VIDEO_DIR).glob("*.mp4"))
    if not videos:
        raise RuntimeError("No videos found in videos/ directory.")

    for v in videos:
        stem = v.stem
        per_video_coco = [p for p in split_exports if p.stem.startswith(stem)]
        if not per_video_coco:
            print(f"No COCO subset for {v.name}")
            continue

        coco_file = per_video_coco[0]
        seqname = stem

        print(f"\n=== Processing {seqname} ===")

        # Auto-detect resolution and fps
        vid_w, vid_h, vid_fps, duration, frame_count = ffprobe_info(v)

        if USE_ORIGINAL_RES:
            width, height = vid_w, vid_h
        else:
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT

        print(f"Video info: {vid_w}x{vid_h}, {vid_fps:.2f} FPS, {duration:.1f}s")

        # Build MOT sequence (1 FPS)
        convert_to_mot(coco_file, seqname, width, height, TARGET_FPS)

        # Extract frames at 1 FPS (Option A)
        extract_frames(v, seqname, TARGET_FPS)

        print(f"✔ Completed MOT sequence: {seqname}")
        print(f"   → {OUTPUT_DIR / seqname}")

    print("\n All sequences processed with auto-detected resolution & config.yml.")
