"""Focused interoperability, simple-concat comparison and 1080p timing experiments."""
import argparse
import hashlib
import json
import pathlib
import shutil
import statistics
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--suite-dir", type=pathlib.Path, required=True)
args = parser.parse_args()
BIN = next((ROOT / "deps").glob("ffmpeg-*-win64-gpl-shared-8.1")) / "bin"
FF, FP, CLI = BIN / "ffmpeg.exe", BIN / "ffprobe.exe", ROOT / "build/Release/smart_render.exe"
RUN = ROOT / "test-output" / time.strftime("experiments-%Y%m%d-%H%M%S")
RUN.mkdir(parents=True)
results = {"supplemental": [], "concat_baseline": [], "benchmark": {}}


def run(cmd, expected=True):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, timeout=180)
    if expected and p.returncode:
        raise RuntimeError(f"{cmd}\n{p.stderr.decode(errors='replace')[-3000:]}")
    return p


def info(path):
    return json.loads(run([FP, "-v", "error", "-show_streams", "-show_packets", "-of", "json", path]).stdout)


def hashes(path, strict=True, extra=()):
    p = run([FF, "-v", "error", *(["-xerror", "-err_detect", "explode"] if strict else []),
             *extra, "-i", path, "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "framemd5", "-"], strict)
    rows = [line.split(',')[-1].strip() for line in p.stdout.decode().splitlines() if line and not line.startswith('#')]
    return rows, p


def compare_copy(source, output, plan):
    wanted = [i for unit in plan["units"] for i in range(unit["begin"], unit["end"])]
    original, _ = hashes(source); actual, p = hashes(output)
    assert len(actual) == len(wanted), (len(actual), len(wanted))
    copied = {i for u in plan["units"] if u["mode"] == "copy" for i in range(u["begin"], u["end"])}
    assert all(actual[j] == original[i] for j, i in enumerate(wanted) if i in copied)
    assert not p.stderr.strip(), p.stderr.decode(errors="replace")
    return len(actual)


base = args.suite_dir / "source-closed_b3_id0.mp4"
# Audio first exercises nonzero video stream indexes; audio is intentionally discarded.
audio = RUN / "音频在前 输入.mp4"
run([FF, "-v", "error", "-i", base, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=9.6",
     "-map", "1:a:0", "-map", "0:v:0", "-c:v", "copy", "-c:a", "aac", "-shortest", audio])
for mode in ("avc3", "stsd"):
    output = RUN / f"中文路径 输出 {mode}.mp4"
    p = run([CLI, "-i", audio, "-o", output, "--keep", "13:227", "--ps-mode", mode])
    plan = json.loads(p.stdout); compare_copy(base, output, plan)
    streams = info(output)["streams"]
    assert len(streams) == 1 and streams[0]["codec_type"] == "video"
    results["supplemental"].append({"case": "unicode_paths_audio_first_ignored", "mode": mode, "status": "pass"})

# Seek into each retained IDR: the output must be self-contained at advertised sync samples.
for mode in ("avc3", "stsd"):
    path = args.suite_dir / f"closed_b3_id0-both_edges-{mode}-crf18.mp4"
    data = info(path); stream = data["streams"][0]
    from fractions import Fraction
    tb = Fraction(stream["time_base"])
    full, _ = hashes(path)
    presented = sorted(data["packets"], key=lambda x: int(x["pts"]))
    for j, packet in enumerate(presented):
        if 'K' not in packet["flags"]:
            continue
        sec = int(packet["pts"]) * tb
        sought, p = hashes(path, extra=("-ss", f"{float(sec):.9f}"))
        assert sought == full[j:], f"Random access differs at frame {j} ({mode})"
    results["supplemental"].append({"case": "seek_all_advertised_idrs", "mode": mode, "status": "pass"})

# A corrupt NAL length must be rejected, rather than producing a success-looking output.
data = info(base); first = data["packets"][0]; corrupt = bytearray(base.read_bytes())
pos = int(first["pos"]); corrupt[pos:pos + 4] = b'\xff\xff\xff\xff'
bad = RUN / "bad-length.mp4"; bad.write_bytes(corrupt); bad_out = RUN / "must-not-exist.mp4"
p = run([CLI, "-i", bad, "-o", bad_out, "--keep", "0:100"], False)
assert p.returncode and not bad_out.exists() and not list(RUN.glob("must-not-exist.mp4.partial-*"))
results["supplemental"].append({"case": "corrupt_nal_rejected_no_output", "status": "pass"})

# This is a command-level recipe, NOT a run of the LosslessCut GUI or its exact implementation.
for name in ("closed_b3_id0", "open_gop", "high10"):
    source = args.suite_dir / f"source-{name}.mp4"
    if not source.exists():
        continue
    source_info = info(source); stream = source_info["streams"][0]
    head, tail, output = [RUN / f"baseline-{name}-{part}.mp4" for part in ("head", "tail", "output")]
    # Accurate 13..24 head + stream-copy 24..227 tail; deliberately no custom GOP/parameter-set logic.
    run([FF, "-v", "error", "-ss", "0.52", "-i", source, "-t", "0.44", "-map", "0:v:0", "-c:v", "libx264",
         "-crf", "18", "-video_track_timescale", "12800", head])
    run([FF, "-v", "error", "-ss", "0.96", "-i", source, "-t", "8.12", "-map", "0:v:0", "-c:v", "copy", tail])
    listing = RUN / f"concat-{name}.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in (head, tail)), encoding="utf-8")
    p = run([FF, "-v", "warning", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", output], False)
    record = {"fixture": name, "mux_exit": p.returncode, "mux_log": p.stderr.decode(errors="replace")[:1500]}
    if output.exists():
        decoded, dec = hashes(output, False); expected, _ = hashes(source)
        # Expected copied frames 24..226, each compared against its intended output position.
        matches = sum(j < len(decoded) and decoded[j] == expected[i] for j, i in enumerate(range(13, 227)) if i >= 24)
        record.update({"decoded_frames": len(decoded), "expected_frames": 214, "copy_matches_at_expected_positions": matches,
                       "expected_copy_frames": 203, "decode_exit": dec.returncode, "decode_log": dec.stderr.decode(errors="replace")[:1500]})
    results["concat_baseline"].append(record)
    print("BASELINE", name, record.get("decoded_frames"), "frames; copy matches", record.get("copy_matches_at_expected_positions"), flush=True)

# A 600-frame 1080p workload. Source generation and verification are outside the measured interval.
source = RUN / "benchmark-1080p.mp4"
run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=20", "-an",
     "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-x264-params", "keyint=60:min-keyint=60:scenecut=0:open-gop=0:bframes=3", source])
smart_times, full_times = [], []
for iteration in range(3):
    smart = RUN / f"benchmark-smart-{iteration}.mp4"
    start = time.perf_counter(); p = run([CLI, "-i", source, "-o", smart, "--keep", "13:587"])
    smart_times.append(time.perf_counter() - start); plan = json.loads(p.stdout)
    if iteration == 0:
        compare_copy(source, smart, plan)
    full = RUN / f"benchmark-full-{iteration}.mp4"
    start = time.perf_counter()
    run([FF, "-v", "error", "-i", source, "-map", "0:v:0", "-vf", "trim=start_frame=13:end_frame=587,setpts=PTS-STARTPTS",
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-bf", "0", "-movflags", "+faststart", full])
    full_times.append(time.perf_counter() - start)
    if iteration == 0:
        assert len(hashes(full)[0]) == 574
    print("BENCHMARK", iteration, "smart", smart_times[-1], "full", full_times[-1], flush=True)
results["benchmark"] = {"width": 1920, "height": 1080, "source_frames": 600, "output_frames": 574,
                         "copied_frames": plan["copied_frames"], "encoded_frames": plan["encoded_frames"],
                         "smart_seconds": smart_times, "full_encode_seconds": full_times,
                         "smart_median_seconds": statistics.median(smart_times), "full_median_seconds": statistics.median(full_times),
                         "speedup": statistics.median(full_times) / statistics.median(smart_times),
                         "note": "Local synthetic workload, warm filesystem cache; not an encoder/quality-equivalence benchmark."}
(RUN / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print("RESULTS", RUN / "results.json", flush=True)
