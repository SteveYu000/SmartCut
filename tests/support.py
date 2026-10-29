"""Locate the configured FFmpeg SDK and reuse the independent research oracle.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sdk_path():
    if os.environ.get('FFMPEG_ROOT'):
        return Path(os.environ['FFMPEG_ROOT'])
    cache = ROOT / 'build/CMakeCache.txt'
    if cache.exists():
        for line in cache.read_text(encoding='utf-8').splitlines():
            if line.startswith('FFMPEG_INCLUDE_DIR:PATH='):
                return Path(line.split('=', 1)[1]).parent
    candidates = sorted((ROOT / 'deps').glob('ffmpeg-*'))
    candidates += sorted((ROOT / 'research/h264/deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))
    if not candidates:
        raise RuntimeError('Configure CMake with -DFFMPEG_ROOT=<shared SDK>, or set FFMPEG_ROOT.')
    return candidates[0]


SDK = sdk_path()
FF = SDK / 'bin/ffmpeg.exe'
FP = SDK / 'bin/ffprobe.exe'


def load_oracle():
    spec = importlib.util.spec_from_file_location('product_research_oracle', ROOT / 'research/losslesscut-comparison/run_suite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Override tool paths only. The archived checker and its assertions remain
    # unchanged; no LosslessCut process or HTTP endpoint is used by these tests.
    module.FFMPEG, module.FFPROBE = FF, FP
    module.hevc.FFMPEG, module.hevc.FFPROBE = FF, FP
    os.environ['FFMPEG_ROOT'] = str(SDK)
    return module
