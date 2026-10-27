"""Fetch a pinned public FFmpeg SDK, resume interrupted transfers and verify its digest."""
import hashlib
import json
import pathlib
import time
import urllib.request
import zipfile

root = pathlib.Path(__file__).resolve().parents[1] / "deps"
root.mkdir(exist_ok=True)
asset = json.loads(pathlib.Path(__file__).with_name("ffmpeg-sdk.json").read_text(encoding="utf-8-sig"))
archive = root / "ffmpeg.zip"
for attempt in range(16):
    start = archive.stat().st_size if archive.exists() else 0
    if start == asset["size"]:
        break
    try:
        req = urllib.request.Request(asset["browser_download_url"], headers={"Range": f"bytes={start}-"})
        with urllib.request.urlopen(req, timeout=30) as response:
            resume = response.status == 206
            if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {start}-"):
                raise ValueError("Unexpected Content-Range")
            with archive.open("ab" if resume else "wb") as target:
                while block := response.read(1024 * 1024):
                    target.write(block)
        print("Downloaded", archive.stat().st_size, flush=True)
    except Exception as exc:
        print(f"Attempt {attempt + 1}: {type(exc).__name__}; partial={archive.stat().st_size if archive.exists() else 0}", flush=True)
        time.sleep(2)
digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
if archive.stat().st_size != asset["size"] or digest != asset["digest"]:
    raise RuntimeError(f"SDK download failed verification: {digest}")
with zipfile.ZipFile(archive) as sdk:
    sdk.extractall(root)
print("Verified and extracted", digest, flush=True)
