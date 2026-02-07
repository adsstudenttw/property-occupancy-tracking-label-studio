import os
import json
import yaml
import requests
import subprocess
from pathlib import Path
from collections import defaultdict

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

CONFIG = yaml.safe_load(open("config.yml"))

PROJECT_ID = CONFIG["label_studio"]["project_id"]

EXPORT_DIR = Path(CONFIG["exports"]["export_dir"])
OUTPUT_DIR = Path(CONFIG["mot"]["output_dir"])
VIDEO_DIR = Path(CONFIG["videos"]["directory"])

FRAME_STRIDE = CONFIG["video_processing"]["frame_stride"]
MOT_FPS = CONFIG["video_processing"]["mot_fps"]
USE_ORIGINAL_RES = CONFIG["video_processing"]["use_original_resolution"]
DEFAULT_WIDTH = CONFIG["video_processing"]["default_width"]
DEFAULT_HEIGHT = CONFIG["video_processing"]["default_height"]

LS_URL = os.getenv("LABEL_STUDIO_URL")
API_KEY = os.getenv("LABEL_STUDIO_API_KEY")

if not LS_URL or not API_KEY:
    raise RuntimeError("LABEL_STUDIO_URL or LABEL_STUDIO_API_KEY not set")


# VIDEO METADATA


def ffprobe_info(video_path: Path):
    """
    Auto-detect width, height, fps, duration using ffprobe
    """
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
    data = json.loads(subprocess.check_output(cmd).decode())

    stream = next(s for s in data["streams"] if s["codec_type"] == "video")

    width = int(stream["width"])
    height = int(stream["height"])

    r_num, r_den = stream["r_frame_rate"].split("/")
    fps = float(r_num) / float(r_den)

    duration = float(data["format"]["duration"])
    total_frames = int(duration * fps)

    return width, height, fps, duration, total_frames


# LABEL STUDIO EXPORT (JSON)


def fetch_ls_json_export():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    url = f"{LS_URL}/api/projects/{PROJECT_ID}/export"
    headers = {
        "Authorization": f"Token {API_KEY}",
    }
    params = {
        "exportType": "JSON",  # ← THIS MUST BE exportType
    }

    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()

    out = EXPORT_DIR / f"project_{PROJECT_ID}_ls.json"
    out.write_bytes(r.content)

    print(f"Label Studio JSON export saved: {out}")
    return out


# LS JSON -> MOT GT


def parse_ls_tracks(ls_json_path: Path):
    """
    Parse Label Studio VIDEO JSON export into per-video tracks.

    Label Studio (current JSON export) does NOT include value.track_id.
    Each tracked object is represented by one `result` item of type
    'videorectangle' with:
      - result["id"]  -> stable unique track key
      - value.sequence -> list of keyframes for that track

    Returns:
        dict[video_name][track_key] = list of (ls_frame, x, y, w, h)
        where:
          - ls_frame is 1-based (as exported by Label Studio)
          - x,y,w,h are percentages (0..100)
    """
    data = json.loads(ls_json_path.read_text())
    tracks_per_video = defaultdict(lambda: defaultdict(list))

    for task in data:
        video_url = task.get("data", {}).get("video")
        if not video_url:
            continue

        raw_name = Path(video_url).stem

        # Label Studio often prefixes uploaded files: "<uuid>-<original>"
        video_name = raw_name.split("-", 1)[1] if "-" in raw_name else raw_name

        for ann in task.get("annotations", []):
            for result in ann.get("result", []):
                if result.get("type") != "videorectangle":
                    continue

                track_key = result.get("id")  # ✅ stable unique identifier per track
                if not track_key:
                    # Extremely defensive fallback (shouldn't happen)
                    track_key = f"{task.get('id')}-{result.get('from_name')}-{result.get('to_name')}"

                value = result.get("value", {})
                seq = value.get("sequence", [])

                for kf in seq:
                    # Skip disabled keyframes (track ended / hidden)
                    if not kf.get("enabled", True):
                        continue

                    ls_frame = int(kf["frame"])  # 1-based frame index in LS export
                    x = float(kf["x"])
                    y = float(kf["y"])
                    w = float(kf["width"])
                    h = float(kf["height"])

                    tracks_per_video[video_name][track_key].append(
                        (ls_frame, x, y, w, h)
                    )

    # Sort each track by LS frame index for consistency
    for vname in tracks_per_video:
        for tkey in tracks_per_video[vname]:
            tracks_per_video[vname][tkey].sort(key=lambda tup: tup[0])

    return tracks_per_video


def write_mot_sequence(
    seqname: str,
    video_path: Path,
    tracks: dict,
):
    """
    Convert Label Studio video annotations into a MOTChallenge sequence.

    Assumptions (by design):
    - Annotation video FPS = 24
    - Annotations are placed every 24 frames
    - Frames are extracted using: select=not(mod(n,24))
    - Therefore:
        MOT frame index = (ls_frame // 24) + 1

    This guarantees exact alignment between:
    - Label Studio frames
    - Extracted images
    - MOT gt.txt
    """

    # Video metadata (used for resolution & seqinfo)
    vid_w, vid_h, vid_fps, duration, total_frames = ffprobe_info(video_path)

    if USE_ORIGINAL_RES:
        width, height = vid_w, vid_h
    else:
        width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT

    # Output directories
    seq_dir = OUTPUT_DIR / seqname
    gt_dir = seq_dir / "gt"
    det_dir = seq_dir / "det"
    img_dir = seq_dir / "img1"
    gt_dir.mkdir(parents=True, exist_ok=True)
    det_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    # MOT seqinfo.ini
    # For 0-based n in [0, total_frames-1], count multiples of FRAME_STRIDE.
    seq_length = ((total_frames - 1) // FRAME_STRIDE) + 1 if total_frames > 0 else 0

    # seqinfo.ini
    (seq_dir / "seqinfo.ini").write_text(
        f"""[Sequence]
        name={seqname}
        imDir=img1
        frameRate={MOT_FPS}
        seqLength={seq_length}
        imWidth={width}
        imHeight={height}
        imExt=.jpg
        """
    )

    # Map track keys (strings) -> integer MOT IDs
    track_id_map = {k: i + 1 for i, k in enumerate(sorted(tracks.keys(), key=str))}

    gt_lines = []
    det_lines = []

    for track_key, frames in tracks.items():
        mot_id = track_id_map[track_key]

        for ls_frame, x, y, w, h in frames:
            # LS frames are 1-based; convert to 0-based before stride mapping
            ls_zero = ls_frame - 1

            # Exact stride mapping: LS frame 1->0 maps to MOT frame 1
            mot_frame = (ls_zero // FRAME_STRIDE) + 1

            # Percent -> pixels
            px = x / 100.0 * width
            py = y / 100.0 * height
            pw = w / 100.0 * width
            ph = h / 100.0 * height

            gt_lines.append(
                f"{mot_frame},{mot_id},"
                f"{px:.1f},{py:.1f},{pw:.1f},{ph:.1f},"
                f"1,-1,-1,-1"
            )

            det_lines.append(
                f"{mot_frame},-1,"
                f"{px:.1f},{py:.1f},{pw:.1f},{ph:.1f},"
                f"1.0,-1,-1,-1"
            )

    # Sort by frame (and then by id for determinism)
    gt_lines.sort(key=lambda line: (int(line.split(",")[0]), int(line.split(",")[1])))
    det_lines.sort(key=lambda line: int(line.split(",")[0]))

    (gt_dir / "gt.txt").write_text("\n".join(gt_lines))
    (det_dir / "det.txt").write_text("\n".join(det_lines))

    # Extract frames: EXACTLY every FRAME_STRIDE-th decoded frame (0, stride, 2*stride, ...)
    # setpts is crucial to avoid “1-frame early” drift after the first frame.
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            f"select=not(mod(n\\,{FRAME_STRIDE})),setpts=N/FRAME_RATE/TB",
            "-fps_mode",
            "vfr",
            "-qscale:v",
            "2",
            "-start_number",
            "1",
            str(img_dir / "%06d.jpg"),
        ],
        check=True,
    )

    print(f"Completed MOT sequence: {seqname}")
    print(f"  -> {seq_dir}")


# MAIN

if __name__ == "__main__":
    ls_json = fetch_ls_json_export()
    tracks_by_video = parse_ls_tracks(ls_json)

    videos = list(Path(VIDEO_DIR).glob("*.mp4"))
    if not videos:
        raise RuntimeError("No videos found in videos/ directory.")

    for video in videos:
        seqname = video.stem
        print(f"\n=== Processing {seqname} ===")

        if seqname not in tracks_by_video:
            print("No annotations found, skipping.")
            continue

        write_mot_sequence(
            seqname=seqname,
            video_path=video,
            tracks=tracks_by_video[seqname],
        )

    print("\n🎉 All sequences processed (LS JSON -> MOTChallenge).")
