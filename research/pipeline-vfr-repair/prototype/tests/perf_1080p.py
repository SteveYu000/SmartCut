"""Time the pipeline against a full re-encode of the same selection.

Run: python tests/perf_1080p.py --source BENCH.mp4 --out docs/evidence/perf-1080p.json
Both arms use the same x264 settings; each arm runs three times.
"""
import argparse
import json
import os
import statistics
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--exe", default=os.path.join(os.path.dirname(__file__), "..",
                                                  "src/SmartCut.Cli/bin/Release/net10.0/SmartCut.Cli.exe"))
parser.add_argument("--keep", default="13:587")
parser.add_argument("--frames", default="13:586", help="inclusive:exclusive display-frame window for the ffmpeg arm")
parser.add_argument("--ffmpeg", default="ffmpeg")
parser.add_argument("--out", required=True)
parser.add_argument("--work", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "perf-work"))
args = parser.parse_args()

ENCODER = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-bf", "0", "-g", "100000",
           "-x264-params", "scenecut=0:open-gop=0:bframes=0:keyint=100000", "-pix_fmt", "yuv420p", "-an"]
os.makedirs(args.work, exist_ok=True)
begin, end = (int(value) for value in args.keep.split(":"))


def run(command):
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise SystemExit(result.stderr.decode(errors="replace")[-1500:])
    return time.perf_counter() - started, result


pipeline, full, report = [], [], {}
for index in (1, 2, 3):
    output = os.path.join(args.work, f"pipeline-{index}.mp4")
    if os.path.exists(output):
        os.remove(output)
    seconds, result = run([args.exe, "-i", args.source, "-o", output, "--keep", args.keep, "--no-audio"])
    payload = json.loads(result.stdout.decode())
    report[f"pipeline_run{index}"] = {"seconds": round(seconds, 3),
                                      **{key: payload[key] for key in ("copied_frames", "encoded_frames",
                                                                       "expected_frames", "segments",
                                                                       "elapsed_seconds", "duration_ticks")}}
    pipeline.append(seconds)

    output = os.path.join(args.work, f"full-{index}.mp4")
    if os.path.exists(output):
        os.remove(output)
    seconds, _ = run([args.ffmpeg, "-v", "error", "-y", "-i", args.source,
                      "-vf", f"select='between(n,{begin},{end - 1})'", "-fps_mode", "passthrough",
                      *ENCODER, output])
    full.append(seconds)
    report[f"full_encode_run{index}"] = {"seconds": round(seconds, 3)}

evidence = {
    "source": os.path.basename(args.source) + " (1080p, 30 fps, 600 frames, x264)",
    "keep": args.keep,
    "pipeline_seconds": [round(x, 3) for x in pipeline],
    "full_encode_seconds": [round(x, 3) for x in full],
    "pipeline_median": round(statistics.median(pipeline), 3),
    "full_encode_median": round(statistics.median(full), 3),
    "ratio": round(statistics.median(full) / statistics.median(pipeline), 2),
    "pipeline_command": (f"{os.path.relpath(os.path.abspath(args.exe), os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}"
                         f" -i SOURCE -o OUT --keep {args.keep} --no-audio"),
    "full_encode_command": ("ffmpeg -v error -y -i "
                            f"{os.path.basename(args.source)} -vf select='between(n,"
                            f"{begin},{end - 1})' -fps_mode passthrough " + " ".join(ENCODER) + " OUT"),
    "runs": report,
}
os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
with open(args.out, "w", encoding="utf-8") as handle:
    json.dump(evidence, handle, indent=2)
print(json.dumps({k: evidence[k] for k in
                  ("pipeline_seconds", "full_encode_seconds", "pipeline_median", "full_encode_median", "ratio")}, indent=1))
print("evidence:", os.path.abspath(args.out))
