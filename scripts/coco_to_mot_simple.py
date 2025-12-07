#!/usr/bin/env python3
import json, csv, argparse, pathlib

p = argparse.ArgumentParser()
p.add_argument("--coco", required=True)
p.add_argument("--outdir", required=True)
p.add_argument("--seqname", default="seq01")
p.add_argument("--width", type=int, required=True)
p.add_argument("--height", type=int, required=True)
# Option A: 1 FPS by default
p.add_argument("--fps", type=int, default=1)
args = p.parse_args()

coco = json.load(open(args.coco))
images = sorted(coco["images"], key=lambda x: x["id"])
image_id_to_frame = {img["id"]: i + 1 for i, img in enumerate(images)}

out = pathlib.Path(args.outdir) / args.seqname / "gt"
out.mkdir(parents=True, exist_ok=True)
gt = out / "gt.txt"

rows = []
for ann in coco["annotations"]:
    frame = image_id_to_frame[ann["image_id"]]
    track_id = int(ann.get("track_id", ann["id"]))
    x, y, w, h = ann["bbox"]
    # MOT is 1-based for coordinates → +1
    rows.append([frame, track_id, x + 1, y + 1, w, h, 1, -1, -1, -1])

rows.sort(key=lambda r: (r[0], r[1]))

with gt.open("w") as f:
    csv.writer(f).writerows(rows)

seqinfo = pathlib.Path(args.outdir) / args.seqname / "seqinfo.ini"
seqinfo.write_text(
    f"[Sequence]\n"
    f"name={args.seqname}\n"
    f"imDir=img1\n"
    f"frameRate={args.fps}\n"
    f"seqLength={len(images)}\n"
    f"imWidth={args.width}\n"
    f"imHeight={args.height}\n"
    f"imExt=.jpg\n"
)

print("MOTChallenge sequence created at 1 FPS.")
