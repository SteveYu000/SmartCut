"""Regenerate the original 240-frame research fixtures without running its tests.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import numpy as np
from support import ROOT, FF, SDK

parser = argparse.ArgumentParser()
parser.add_argument('--with-nvenc', action='store_true', help='Generate NVIDIA input fixtures too (requires a working NVIDIA encoder)')
args = parser.parse_args()

# Read only the generator definitions and their constants. Importing the old
# H.264 integration script would execute its historical CLI/tests at top level.
path = ROOT / 'research/h264/tests/integration.py'
tree = ast.parse(path.read_text(encoding='utf-8-sig'))
nodes = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == 'rgb_frames':
        nodes.append(node)
    if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id in ('BASE', 'specs') for n in node.targets):
        nodes.append(node)
namespace = {'np': np, 'W': 192, 'H': 112, 'N': 240}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
raw = namespace['rgb_frames']()
manifest = {'sdk': str(SDK), 'fixtures': []}
folder = ROOT / 'research/h264/test-data/run-20261027-125923'
folder.mkdir(parents=True, exist_ok=True)
for name, (params, extra, pix, rate) in namespace['specs'].items():
    if name == 'nvenc' and not args.with_nvenc:
        continue
    target = folder / ('source-' + name + '.mp4')
    if not target.exists():
        command = [FF, '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '192x112',
                   '-framerate', rate, '-i', '-', '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '14',
                   '-pix_fmt', pix, '-x264-params', params, *extra, target]
        p = subprocess.run(list(map(str, command)), input=raw, capture_output=True, timeout=180)
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors='replace'))
    manifest['fixtures'].append({'codec': 'h264', 'name': name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    print('h264', name, flush=True)

spec = importlib.util.spec_from_file_location('hevc_fixture_generator', ROOT / 'research/h265/tests/common.py')
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
common.FFMPEG = FF
folder = ROOT / 'research/h265/test-data/fixtures'
folder.mkdir(parents=True, exist_ok=True)
for name in common.SPECS:
    if name == 'nvenc' and not args.with_nvenc:
        continue
    target = folder / ('source-' + name + '.mp4')
    if not target.exists():
        common.fixture(folder, name)
    manifest['fixtures'].append({'codec': 'h265', 'name': name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    print('h265', name, flush=True)
manifest_path = ROOT / 'research/product-validation/regenerated-fixtures.json'
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
