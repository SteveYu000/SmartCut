"""Package this tested workspace without overwriting any existing release.

Usage: python scripts/package_delivery.py --suite-dir test-output/run-...
       --experiments-dir test-output/experiments-...
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--suite-dir", type=Path, required=True)
parser.add_argument("--experiments-dir", type=Path, required=True)
parser.add_argument("--destination", type=Path, default=ROOT.parent / "deliverables")
args = parser.parse_args()
suite, experiments = args.suite_dir.resolve(), args.experiments_dir.resolve()
destination = args.destination.resolve()
destination.mkdir(parents=True, exist_ok=True)
release = destination / "h264-smart-render-win64"
if release.exists():
    raise RuntimeError(f"Refusing to overwrite {release}")
sdk = next((ROOT / "deps").glob("ffmpeg-*-win64-gpl-shared-8.1"))
build = ROOT / "build/Release"
evidence = ROOT / "docs/evidence"
evidence.mkdir(exist_ok=True)
records = json.loads((suite / "results.json").read_text())
assert all(counts["fail"] == 0 for counts in records["summary"].values())
extra = json.loads((experiments / "results.json").read_text())
assert all(item["status"] == "pass" for item in extra["supplemental"])
for source, name in ((suite / "results.json", "final-integration.json"),
                     (experiments / "results.json", "final-experiments.json")):
    shutil.copy2(source, evidence / name)
for directory, name in (("run-20261026-232806", "initial-lossless-failures.json"),
                        ("run-20261026-233356", "diagnostic-alternate-id.json")):
    source = ROOT / "test-output" / directory / "results.json"
    if source.exists():
        shutil.copy2(source, evidence / name)
manifest_path = ROOT.parent / "research/source_manifest.json"
if manifest_path.exists():
    shutil.copy2(manifest_path, evidence / "source-manifest.json")
licenses = ROOT / "licenses"
licenses.mkdir(exist_ok=True)
shutil.copy2(sdk / "LICENSE.txt", licenses / "FFmpeg-GPL-3.0.txt")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


runtime = ("smart_render.exe", "avcodec-62.dll", "avformat-62.dll", "avutil-60.dll", "swresample-6.dll")
manifest = {
    "packaged_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    "platform": "Windows x64 build 26200", "compiler": "MSVC 19.51.36260 / VS 2026 Professional",
    "cpu": "Intel Core Ultra 9 275HX", "fixture_gpu": "NVIDIA GeForce RTX 5060 Laptop GPU",
    "ffmpeg": json.loads((suite / "closed_b3_id0-both_edges-avc3-crf18.json").read_text())["ffmpeg"],
    "sdk": json.loads((ROOT / "scripts/ffmpeg-sdk.json").read_text()),
    "sources": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted((ROOT / "src").glob("*"))},
    "tests": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted((ROOT / "tests").glob("*.py"))},
    "build_files": {name: sha(ROOT / name) for name in ("CMakeLists.txt", "CMakePresets.json")},
    "runtime": {name: {"bytes": (build / name).stat().st_size, "sha256": sha(build / name)} for name in runtime},
    "final_suite": str(suite), "final_experiments": str(experiments),
}
(evidence / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

for doc in [*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md")]:
    for target in re.findall(r"\]\(([^)]+)\)", doc.read_text(encoding="utf-8")):
        if not target.startswith(("https://", "http://", "#")):
            assert (doc.parent / target.split("#")[0]).exists(), (doc, target)
release.mkdir()
for name in runtime:
    shutil.copy2(build / name, release / name)
samples = release / "samples"
samples.mkdir()
shutil.copy2(suite / "source-closed_b3_id0.mp4", samples / "input.mp4")
shutil.copy2(suite / "closed_b3_id0-both_edges-avc3-crf18.mp4", samples / "expected-avc3.mp4")
shutil.copy2(suite / "closed_b3_id0-both_edges-avc3-crf18.json", samples / "expected-plan.json")
subprocess.run([sys.executable, str(ROOT / "tests/verify_delivery.py"), str(release), str(suite)], check=True)
for name in ("README.md", "THIRD_PARTY.md"):
    shutil.copy2(ROOT / name, release / name)
for name in ("docs", "licenses"):
    shutil.copytree(ROOT / name, release / name)

source_files = [ROOT / name for name in ("CMakeLists.txt", "CMakePresets.json", "README.md", "THIRD_PARTY.md", "requirements-test.txt", ".gitignore")]
for directory in ("src", "scripts", "tests", "docs", "licenses"):
    source_files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
source_zip = destination / "h264-smart-render-source.zip"
with zipfile.ZipFile(source_zip, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for p in sorted(source_files):
        archive.write(p, "h264-smart-render/" + p.relative_to(ROOT).as_posix())
with zipfile.ZipFile(source_zip, "r") as archive:
    assert archive.testzip() is None
runtime_zip = destination / "h264-smart-render-win64.zip"
with zipfile.ZipFile(runtime_zip, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for p in sorted(release.rglob("*")):
        if p.is_file():
            archive.write(p, "h264-smart-render-win64/" + p.relative_to(release).as_posix())
with zipfile.ZipFile(runtime_zip, "r") as archive:
    assert archive.testzip() is None
report = (ROOT / "docs/IMPLEMENTATION_REPORT.md").read_text(encoding="utf-8")
report = report.replace("](evidence/", "](h264-smart-render-win64/docs/evidence/").replace("](../README.md)", "](h264-smart-render-win64/README.md)")
report_path = destination / "H264_Smart_Rendering实现与测试报告.md"
with report_path.open("x", encoding="utf-8") as f:
    f.write(report)
with (destination / "h264-smart-render-SHA256SUMS.txt").open("x", encoding="utf-8") as f:
    for p in (source_zip, runtime_zip, report_path):
        f.write(f"{sha(p)}  {p.name}\n")
print(json.dumps({"runtime": str(runtime_zip), "source": str(source_zip), "report": str(report_path),
                  "runtime_zip_bytes": runtime_zip.stat().st_size}, indent=2))
