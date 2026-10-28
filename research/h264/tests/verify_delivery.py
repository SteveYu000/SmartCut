"""Check the runtime DLL subset and documented dynamic-configuration rejection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
release, suite = (Path(x).resolve() for x in sys.argv[1:3])
evidence = ROOT / "docs/evidence"
sdk = next((ROOT / "deps").glob("ffmpeg-*-win64-gpl-shared-8.1"))
cli, ff = release / "smart_render.exe", sdk / "bin/ffmpeg.exe"
clean_env = os.environ.copy()
clean_env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")


def invoke(command, env=None):
    p = subprocess.run([str(x) for x in command], capture_output=True, timeout=120, env=env)
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    return p


invoke([cli, "--help"], clean_env)
check_dir = ROOT / "test-output" / time.strftime("package-check-%Y%m%d-%H%M%S")
check_dir.mkdir()
output = check_dir / "cut.mp4"
p = invoke([cli, "-i", release / "samples/input.mp4", "-o", output, "--keep", "13:227"], clean_env)
plan = json.loads(p.stdout)
assert plan["copied_frames"] == 192 and plan["encoded_frames"] == 22
outputs = []
for path in (output, release / "samples/expected-avc3.mp4"):
    p = invoke([ff, "-v", "error", "-xerror", "-err_detect", "explode", "-i", path,
                "-fps_mode", "passthrough", "-f", "framemd5", "-"])
    assert not p.stderr.strip()
    rows = [line for line in p.stdout.decode().splitlines() if line and not line.startswith('#')]
    assert len(rows) == 214
    outputs.append(rows)
assert outputs[0] == outputs[1]
limitations = []
for mode in ("avc3", "stsd"):
    source = suite / f"closed_b3_id0-both_edges-{mode}-crf18.mp4"
    p = subprocess.run([str(cli), "-i", str(source), "--keep", "1:200", "--analyze"], capture_output=True, timeout=30)
    assert p.returncode != 0, "Dynamic-configuration input behavior changed"
    limitations.append({"mode": mode, "exit": p.returncode, "stderr": p.stderr.decode(errors="replace")})
(evidence / "input-limitations.json").write_text(json.dumps(limitations, indent=2), encoding="utf-8")
manifest_file = evidence / "build-manifest.json"
manifest = json.loads(manifest_file.read_text())
manifest["package_smoke"] = {"status": "pass", "output_frames": 214, "copied_frames": 192,
                             "encoded_frames": 22, "all_frame_hashes_match_reference": True,
                             "path": str(check_dir), "path_environment": "Windows System32 only"}
manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("Packaged runtime and dynamic-input rejection: PASS")
