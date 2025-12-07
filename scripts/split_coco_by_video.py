import json
from pathlib import Path
from collections import defaultdict

"""
Split a COCO export from Label Studio into one COCO file per video.

Assumes Label Studio stored frame paths like:
    data/MyVideo.mp4#t=12.3
or a frame identifier that contains the original video filename.
"""


def split_coco_by_video(coco_path, outdir):
    coco = json.load(open(coco_path))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Map: video_name → {images:[], annotations:[], categories:...}
    buckets = defaultdict(
        lambda: {
            "images": [],
            "annotations": [],
            "categories": coco.get("categories", []),
        }
    )

    # Identify video from image file_name field
    image_map = {}
    for img in coco["images"]:
        fname = img["file_name"]  # Example: "myvideo.mp4#t=12.3"
        video_name = fname.split("#")[0]  # everything before "#"
        image_map[img["id"]] = video_name
        buckets[video_name]["images"].append(img)

    # Assign annotations to the same video bucket
    for ann in coco["annotations"]:
        vid = image_map[ann["image_id"]]
        buckets[vid]["annotations"].append(ann)

    # Output separate COCO files
    outputs = []
    for vid, content in buckets.items():
        out = outdir / f"{Path(vid).stem}_coco.json"
        json.dump(content, open(out, "w"), indent=2)
        outputs.append(out)

    return outputs


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--coco", required=True)
    p.add_argument("--outdir", required=True)
    args = p.parse_args()
    outs = split_coco_by_video(args.coco, args.outdir)
    print("✔ Split into per-video COCO files:")
    for o in outs:
        print("  ", o)
