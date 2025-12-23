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
    Parse Label Studio video annotations.

    Returns:
        dict[video_name][track_id] = list of (frame_idx, x, y, w, h)
    """
    data = json.loads(ls_json_path.read_text())

    tracks_per_video = defaultdict(lambda: defaultdict(list))

    for task in data:
        video_url = task["data"].get("video")
        if not video_url:
            continue

        raw_name = Path(video_url).stem

        # Handle Label Studio upload prefix
        if "-" in raw_name:
            video_name = raw_name.split("-", 1)[1]
        else:
            video_name = raw_name

        for ann in task.get("annotations", []):
            for result in ann.get("result", []):
                if result.get("type") != "videorectangle":
                    continue

                value = result["value"]
                track_id = value.get("track_id")
                seq = value.get("sequence", [])

                for frame in seq:
                    frame_idx = frame["frame"]
                    x = frame["x"]
                    y = frame["y"]
                    w = frame["width"]
                    h = frame["height"]

                    tracks_per_video[video_name][track_id].append(
                        (frame_idx, x, y, w, h)
                    )

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
    - Annotations are placed every 12 frames
    - Frames are extracted using: select=not(mod(n,12))
    - Therefore:
        MOT frame index = (ls_frame // 12) + 1

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
    img_dir = seq_dir / "img1"

    gt_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    # MOT seqinfo.ini
    seq_length = (total_frames // 12) + 1  # number of extracted frames

    seqinfo = seq_dir / "seqinfo.ini"
    seqinfo.write_text(
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

    # Build gt.txt
    gt_lines = []

    for track_id, frames in tracks.items():
        for ls_frame, x, y, w, h in frames:
            # --- exact frame mapping
            mot_frame = (ls_frame // FRAME_STRIDE) + 1

            # --- convert normalized coords (%) → pixels
            px = x / 100.0 * width
            py = y / 100.0 * height
            pw = w / 100.0 * width
            ph = h / 100.0 * height

            gt_lines.append(
                f"{mot_frame},{track_id},"
                f"{px:.1f},{py:.1f},{pw:.1f},{ph:.1f},"
                f"1,1,1"
            )

    # MOT requires sorting by frame index
    gt_lines.sort(key=lambda line: int(line.split(",")[0]))

    gt_file = gt_dir / "gt.txt"
    gt_file.write_text("\n".join(gt_lines))

    # Extract frames: EXACTLY every 12th frame
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            f"select=not(mod(n\\,{FRAME_STRIDE}))",
            "-vsync",
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
