"""End-to-end video tests; the oracle uses FFmpeg decode plus independent MP4 sample inspection.

Run: python tests/integration.py --exe build/Release/smart_render.exe
Requires numpy and the pinned SDK (or --ffmpeg-bin).
Every run gets a new directory; no user files are deleted or overwritten.
"""
import argparse
import hashlib
import json
import math
import pathlib
import struct
import subprocess
import time
import traceback
from fractions import Fraction
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "build/Release/smart_render.exe")
parser.add_argument("--ffmpeg-bin", type=pathlib.Path)
parser.add_argument("--quick", action="store_true")
parser.add_argument("--fixtures", help="Comma-separated fixture names for focused reruns")
parser.add_argument("--with-nvenc", action="store_true", help="Also generate NVIDIA hardware-encoded input")
args = parser.parse_args()
BIN = args.ffmpeg_bin or next((ROOT / "deps").glob("ffmpeg-*-win64-gpl-shared-8.1")) / "bin"
FFMPEG, FFPROBE = BIN / "ffmpeg.exe", BIN / "ffprobe.exe"
EXE = args.exe.resolve()
RUN = ROOT / "test-output" / time.strftime("run-%Y%m%d-%H%M%S")
RUN.mkdir(parents=True, exist_ok=False)
RESULTS = {"run": str(RUN), "cases": [], "negative_tests": [], "ablations": [], "fixtures": {}}
W, H, N = 192, 112, 240


def process(command, data=None, ok=True):
    r = subprocess.run([str(x) for x in command], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if ok and r.returncode:
        raise RuntimeError(f"Exit {r.returncode}: {command}\n{r.stderr.decode('utf-8', errors='replace')[-5000:]}")
    return r


def probe(path):
    r = process([FFPROBE, "-v", "error", "-show_streams", "-show_packets", "-of", "json", path])
    d = json.loads(r.stdout)
    videos = [s for s in d["streams"] if s["codec_type"] == "video"]
    s = videos[0]
    packets = [p for p in d["packets"] if p["stream_index"] == s["index"]]
    return s, packets, d["streams"]


def decode(path, stream, strict=True):
    pix = stream["pix_fmt"]
    # All fixtures preserve 4:2:0 and either 8 or 10 bits.
    pix = "yuv420p10le" if "10" in pix else "yuvj420p" if pix == "yuvj420p" else "yuv420p"
    command = [FFMPEG, "-v", "error"] + (["-xerror", "-err_detect", "explode"] if strict else [])
    r = process(command + ["-i", path, "-map", "0:v:0", "-fps_mode", "passthrough", "-pix_fmt", pix,
                           "-f", "rawvideo", "-"], ok=strict)
    if strict and r.stderr.strip():
        raise AssertionError("Decoder logged errors: " + r.stderr.decode(errors="replace")[-2000:])
    dtype = np.uint16 if "10" in pix else np.uint8
    frame_size = int(stream["width"]) * int(stream["height"]) * 3 // 2
    array = np.frombuffer(r.stdout, dtype=dtype)
    if len(array) % frame_size:
        raise AssertionError("Partial decoded frame")
    return array.reshape((-1, frame_size)), r


def nals(data, length_size):
    pos = 0
    while pos < len(data):
        size = int.from_bytes(data[pos:pos + length_size], "big")
        pos += length_size
        if size <= 0 or pos + size > len(data):
            raise AssertionError("Invalid sample NAL framing")
        yield pos, data[pos:pos + size]
        pos += size


def sample_hashes(path, stream, packets):
    raw = path.read_bytes()
    result = []
    for p in sorted(packets, key=lambda p: int(p["pts"])):
        start, size = int(p["pos"]), int(p["size"])
        vcl = b"".join(struct.pack(">I", len(n)) + n for _, n in nals(raw[start:start + size], int(stream["nal_length_size"]))
                       if n[0] & 31 in (1, 5))
        result.append(hashlib.sha256(vcl).hexdigest())
    return result


def boxes(raw, start=0, end=None):
    end = len(raw) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", raw, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", raw, pos + 8)[0]; header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise AssertionError("Invalid MP4 box")
        yield kind, pos + header, pos + size
        pos += size


def stsd_entries(path):
    raw = path.read_bytes()
    def descend(a, b):
        for kind, begin, end in boxes(raw, a, b):
            if kind == b"stsd":
                yield struct.unpack_from(">I", raw, begin + 4)[0]
            elif kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                yield from descend(begin, end)
    return list(descend(0, len(raw)))


def rgb_frames():
    y, x = np.mgrid[:H, :W]
    frames = []
    for i in range(N):
        image = np.stack(((x * 3 + y + i * 5) % 256, (y * 5 + i * 3) % 256,
                          ((x ^ y) * 2 + i * 7) % 256), axis=2).astype(np.uint8)
        image[40:64, (i * 3) % (W - 25):(i * 3) % (W - 25) + 25] = [255, 30, 180]
        # Large stable barcode blocks independently identify display-frame order.
        for bit in range(10):
            image[2:18, 4 + bit * 16:18 + bit * 16] = 224 if (i >> bit) & 1 else 32
        frames.append(image.tobytes())
    return b"".join(frames)


RAW = rgb_frames()
BASE = "keyint=24:min-keyint=24:scenecut=0:bframes=3:b-adapt=0:ref=4:open-gop=0"
specs = {
    "closed_b3_id0": (BASE, [], "yuv420p", "25"),
    "closed_b3_id3": (BASE + ":sps-id=3", [], "yuv420p", "25"),
    "baseline": (BASE + ":bframes=0:cabac=0:8x8dct=0", ["-profile:v", "baseline"], "yuv420p", "25"),
    "open_gop": (BASE + ":open-gop=1", [], "yuv420p", "25"),
    "mixed_open_idr": (BASE + ":open-gop=1", ["-force_key_frames", "0,3.84,6.72", "-forced-idr", "1"], "yuv420p", "25"),
    "high10": (BASE, [], "yuv420p10le", "25"),
    "multi_slice": (BASE + ":slices=4", [], "yuv420p", "25"),
    "ntsc": (BASE, [], "yuv420p", "30000/1001"),
    "vfr": (BASE, ["-vf", "setpts=(N+floor(N/3))/(25*TB)", "-fps_mode", "vfr"], "yuv420p", "25"),
    "offset": (BASE, ["-output_ts_offset", "2"], "yuv420p", "25"),
    "fragmented": (BASE, ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"], "yuv420p", "25"),
    "fullrange_sar": (BASE, ["-vf", "setsar=4/3", "-color_range", "pc", "-colorspace", "bt709",
                             "-color_primaries", "bt709", "-color_trc", "bt709"], "yuvj420p", "25"),
    "nvenc": (BASE, ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "constqp", "-qp", "18", "-g", "24", "-bf", "3"], "yuv420p", "25"),
}
if args.quick:
    specs = {name: specs[name] for name in ("closed_b3_id0", "open_gop", "vfr", "high10")}
if not args.with_nvenc and not (args.fixtures and "nvenc" in args.fixtures.split(',')):
    specs.pop("nvenc", None)
if args.fixtures:
    selected_names = set(args.fixtures.split(',')) | {"closed_b3_id0", "high10"}
    specs = {name: value for name, value in specs.items() if name in selected_names}

fixtures = {}
for name, (params, extra, pix, rate) in specs.items():
    path = RUN / f"source-{name}.mp4"
    process([FFMPEG, "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{W}x{H}",
             "-framerate", rate, "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
             "-pix_fmt", pix, "-x264-params", params, *extra, path], RAW)
    s, packets, streams = probe(path)
    decoded, _ = decode(path, s)
    assert len(decoded) == N, (name, len(decoded))
    presentation = sorted(packets, key=lambda p: int(p["pts"]))
    timestamps = [int(p["pts"]) for p in presentation]
    timestamps.append(timestamps[-1] + int(presentation[-1]["duration"]))
    fixtures[name] = (path, s, packets, decoded, timestamps, sample_hashes(path, s, packets))
    RESULTS["fixtures"][name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "frames": len(decoded),
                                 "time_base": s["time_base"], "pix_fmt": s["pix_fmt"], "x264_params": params}
    print("FIXTURE", name, flush=True)


def verify(name, selection, mode, label, crf=18):
    source, ss, sp, expected_raw, times, source_hashes = fixtures[name]
    output = RUN / f"{name}-{label}-{mode}-crf{crf}.mp4"
    command = [EXE, "-i", source, "-o", output, "--ps-mode", mode, "--crf", crf]
    for a, b in selection:
        command += ["--keep", f"{a}:{b}"]
    started = time.perf_counter()
    r = process(command)
    report = json.loads(r.stdout)
    (output.with_suffix(".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
    os, op, streams = probe(output)
    assert len(streams) == 1 and streams[0]["codec_type"] == "video", "Unexpected output tracks"
    for key in ("sample_aspect_ratio", "color_range", "color_space", "color_transfer", "color_primaries"):
        if key in ss:
            assert os.get(key) == ss[key], f"Metadata changed: {key}: {ss[key]} -> {os.get(key)}"
    actual, _ = decode(output, os)
    wanted = [i for a, b in selection for i in range(a, b)]
    assert len(actual) == len(wanted) == len(op), f"Frame count {len(actual)} != {len(wanted)}"
    source_tb, out_tb = Fraction(ss["time_base"]), Fraction(os["time_base"])
    predicted = []; cursor = Fraction(0)
    for a, b in selection:
        predicted += [cursor + (times[i] - times[a]) * source_tb for i in range(a, b)]
        cursor += (times[b] - times[a]) * source_tb
    observed = [int(p["pts"]) * out_tb for p in sorted(op, key=lambda p: int(p["pts"]))]
    assert observed == predicted, "Presentation timestamps differ"
    assert abs(Fraction(os["duration"]) - cursor) <= Fraction(1, 1000000), f"Duration {os['duration']} != {float(cursor)}"
    dts = [int(p["dts"]) for p in op]
    assert all(a < b for a, b in zip(dts, dts[1:])), "DTS not strictly increasing"
    assert all(int(p["dts"]) <= int(p["pts"]) for p in op), "DTS > PTS"
    copy_frames = {i for u in report["units"] if u["mode"] == "copy" for i in range(u["begin"], u["end"])}
    encoded_psnr = []; exact_copies = 0
    hashes = sample_hashes(output, os, op)
    peak = 1023.0 if expected_raw.dtype == np.uint16 else 255.0
    for j, i in enumerate(wanted):
        if i in copy_frames:
            assert np.array_equal(actual[j], expected_raw[i]), f"Copied pixels changed at source frame {i}"
            assert hashes[j] == source_hashes[i], f"Copied VCL changed at source frame {i}"
            exact_copies += 1
        else:
            if crf == 0:
                assert np.array_equal(actual[j], expected_raw[i]), f"Lossless boundary mismatch at {i}"
            diff = actual[j].astype(np.float64) - expected_raw[i]
            mse = float(np.mean(diff * diff)); psnr = 99.0 if mse == 0 else 10 * math.log10(peak * peak / mse)
            assert psnr > 30, f"Low boundary PSNR {psnr:.2f} at {i}"
            encoded_psnr.append(psnr)
        # Decode a frame-number barcode, independent of the CLI's plan and timestamps.
        lum = actual[j, :W * H].reshape(H, W)
        frame_id = sum((int(lum[6:14, 8 + bit * 16:14 + bit * 16].mean() > peak / 2) << bit) for bit in range(10))
        assert frame_id == i, f"Wrong picture/order: barcode {frame_id} != source frame {i}"
    assert exact_copies == report["copied_frames"]
    entries = stsd_entries(output)
    assert os["codec_tag_string"] == ("avc3" if mode == "avc3" else "avc1")
    if mode == "stsd" and report["encoded_frames"]:
        assert entries[0] > 1, "Configuration switching did not create sample descriptions"
    return {"fixture": name, "label": label, "mode": mode, "crf": crf, "ranges": selection,
            "status": "pass", "output_frames": len(actual), "copied_frames": exact_copies,
            "encoded_frames": report["encoded_frames"], "decoded_frames": report["decoded_frames"],
            "idr_epochs": report["idr_epochs"], "non_idr_key_packets": report["non_idr_key_packets"],
            "min_encoded_psnr_db": min(encoded_psnr) if encoded_psnr else None,
            "cli_seconds": report["elapsed_seconds"], "verification_seconds": time.perf_counter() - started,
            "stsd_entries": entries, "output": str(output)}


cuts = {
    "full": [(0, N)],
    "aligned": [(24, 216)],
    "both_edges": [(13, 227)],
    "head_only": [(13, 216)],
    "tail_only": [(24, 227)],
    "single_frame": [(23, 24)],
    "cross_idr": [(23, 25)],
    "multiple": [(1, 29), (47, 121), (191, 240)],
    "adjacent": [(0, 24), (24, 48)],
    "adjacent_inside": [(0, 13), (13, 24)],
}
if args.quick:
    cuts = {k: cuts[k] for k in ("full", "both_edges", "single_frame", "multiple")}
for name in fixtures:
    for label, selection in cuts.items():
        for mode in ("avc3", "stsd"):
            try:
                result = verify(name, selection, mode, label)
                print("PASS", name, label, mode, f"copy={result['copied_frames']} encode={result['encoded_frames']}", flush=True)
            except Exception as exc:
                result = {"fixture": name, "label": label, "mode": mode, "status": "fail", "error": str(exc)}
                print("FAIL", name, label, mode, str(exc)[:300], flush=True)
            RESULTS["cases"].append(result)
            (RUN / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")

for mode in ("avc3", "stsd"):
    for name in ("closed_b3_id0", "high10"):
        try:
            result = verify(name, [(13, 227)], mode, "lossless", 0)
        except Exception as exc:
            result = {"fixture": name, "label": "lossless", "mode": mode, "status": "fail", "error": str(exc)}
        RESULTS["cases"].append(result)
        print(result["status"].upper(), name, "lossless", mode, result.get("error", ""), flush=True)


def negative(label, command, check_text=None):
    r = process(command, ok=False)
    message = r.stderr.decode("utf-8", errors="replace")
    passed = r.returncode != 0 and (check_text is None or check_text in message)
    RESULTS["negative_tests"].append({"label": label, "status": "pass" if passed else "fail", "stderr": message[-1500:]})
    print("PASS" if passed else "FAIL", "negative", label, flush=True)


source = fixtures["closed_b3_id0"][0]
for label, extra in (
    ("empty_range", ["--keep", "5:5"]), ("out_of_bounds", ["--keep", "0:9999"]),
    ("overlap", ["--keep", "0:30", "--keep", "29:40"]),
    ("nan_quality", ["--keep", "0:20", "--crf", "nan"]),
    ("bad_mode", ["--keep", "0:20", "--ps-mode", "bogus"]),
):
    negative(label, [EXE, "-i", source, "-o", RUN / f"rejected-{label}.mp4", *extra])
sentinel = RUN / "existing.mp4"; sentinel.write_bytes(b"DO NOT OVERWRITE")
negative("existing_output", [EXE, "-i", source, "-o", sentinel, "--keep", "1:30"], "already exists")
assert sentinel.read_bytes() == b"DO NOT OVERWRITE"
negative("same_as_input", [EXE, "-i", source, "-o", source, "--keep", "1:30"], "already exists")

multi = RUN / "two-video-streams.mp4"
process([FFMPEG, "-v", "error", "-i", source, "-map", "0:v:0", "-map", "0:v:0", "-c", "copy", multi])
negative("two_video_streams", [EXE, "-i", multi, "-o", RUN / "rejected-multi.mp4", "--keep", "0:24"], "Exactly one")
interlaced = RUN / "interlaced.mp4"
process([FFMPEG, "-v", "error", "-i", source, "-vf", "setfield=tff", "-flags", "+ilme+ildct", "-c:v", "libx264",
         "-x264-params", "tff=1:keyint=24", "-an", interlaced])
negative("interlaced", [EXE, "-i", interlaced, "-o", RUN / "rejected-interlaced.mp4", "--keep", "0:24"], "progressive")
other = RUN / "mpeg4-part2.mp4"
process([FFMPEG, "-v", "error", "-i", source, "-c:v", "mpeg4", "-q:v", "2", "-an", other])
negative("other_codec", [EXE, "-i", other, "-o", RUN / "rejected-codec.mp4", "--keep", "0:24"], "H.264")
lossless_source = RUN / "source-lossless-profile.mp4"
process([FFMPEG, "-v", "error", "-i", source, "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", lossless_source])
negative("lossless_source_profile", [EXE, "-i", lossless_source, "-o", RUN / "rejected-lossless-input.mp4", "--keep", "13:227"], "Supported input profiles")

# Remove only the original SPS/PPS restoration at the first copied IDR. Keep sample sizes/offsets intact.
case = next((r for r in RESULTS["cases"] if r.get("fixture") == "closed_b3_id0" and r.get("label") == "both_edges"
             and r.get("mode") == "avc3" and r["status"] == "pass"), None)
if case:
    original = pathlib.Path(case["output"]); os, op, _ = probe(original)
    plan = json.loads(original.with_suffix(".json").read_text())
    copy_start = next(u["output_start_ticks"] for u in plan["units"] if u["mode"] == "copy")
    tb = Fraction(*plan["time_base"])
    target = next(p for p in op if int(p["pts"]) * Fraction(os["time_base"]) == copy_start * tb)
    raw = bytearray(original.read_bytes()); start, length = int(target["pos"]), int(target["size"]); removed = 0
    for offset, nal in nals(raw[start:start + length], int(os["nal_length_size"])):
        if nal[0] & 31 in (7, 8):
            raw[start + offset:start + offset + len(nal)] = bytes([12]) + bytes([255]) * (len(nal) - 2) + bytes([128])
            removed += 1
    broken = RUN / "ablation-no-parameter-restore.mp4"; broken.write_bytes(raw)
    decoded, r = decode(broken, os, strict=False)
    good, _ = decode(original, os)
    detected = r.returncode != 0 or bool(r.stderr.strip()) or decoded.shape != good.shape or not np.array_equal(decoded, good)
    RESULTS["ablations"].append({"label": "remove_parameter_restore", "status": "pass" if removed >= 2 and detected else "fail",
                                 "removed_parameter_nals": removed, "decoder_exit": r.returncode,
                                 "decoder_log": r.stderr.decode(errors="replace")[:1500], "corruption_detected": detected})
    print("ABLATION", "restoration is necessary:", detected, flush=True)

RESULTS["summary"] = {key: {"pass": sum(r["status"] == "pass" for r in RESULTS[key]),
                            "fail": sum(r["status"] == "fail" for r in RESULTS[key])}
                      for key in ("cases", "negative_tests", "ablations")}
(RUN / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
print(json.dumps(RESULTS["summary"], indent=2), flush=True)
print("RESULTS", RUN / "results.json", flush=True)
raise SystemExit(1 if any(v["fail"] for v in RESULTS["summary"].values()) else 0)
