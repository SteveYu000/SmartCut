"""Preserve two minimal failures without changing the implementations or thresholds."""
import json
import subprocess
import wave
from fractions import Fraction
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SDK = ROOT.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
FFMPEG, FFPROBE = SDK / 'ffmpeg.exe', SDK / 'ffprobe.exe'
CPP = ROOT.parent/'h264/build/Release/smart_render.exe'
CS = ROOT / 'artifacts/bin/SmartCut.Cli/release/SmartCut.Cli.exe'
FIXTURES = REPO / 'h264/test-data/run-20261027-125923'
WORK = ROOT / 'diagnostics'
WORK.mkdir(exist_ok=True)

def run(command, required=True):
    p = subprocess.run([str(x) for x in command], capture_output=True)
    if required and p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[-1500:])
    return p

def probe(path, stream='v:0'):
    return json.loads(run([FFPROBE, '-v', 'error', '-select_streams', stream,
                          '-show_entries', 'stream=time_base,duration:packet=pts,dts,duration', '-of', 'json', path]).stdout)

def video_times(path):
    data = probe(path)
    tb = Fraction(data['streams'][0]['time_base'])
    return {'pts_seconds': [float(p['pts'] * tb) for p in sorted(data['packets'], key=lambda p: p['pts'])],
            'stream_duration': data['streams'][0].get('duration'),
            'packets': data['packets'], 'time_base': str(tb)}

source = FIXTURES / 'source-vfr.mp4'
vfr_dir = WORK / 'vfr'
vfr_dir.mkdir(exist_ok=True)
vfr_output = vfr_dir / 'pipeline.mp4'
p = run([CS, '-i', source, '-o', vfr_output, '--keep', '23:25', '--no-audio',
         '--ffmpeg-dir', SDK, '--work-dir', vfr_dir, '--keep-work', '--verify',
         '--report', vfr_dir / 'pipeline.json'], required=False)
(vfr_dir / 'pipeline.log').write_bytes(p.stderr)
cpp_output = vfr_dir / 'h264.mp4'
run([CPP, '-i', source, '-o', cpp_output, '--keep', '23:25', '--report', vfr_dir / 'h264.json'])
src = video_times(source)
vfr = {'pipeline_exit': p.returncode,
       'expected_pts_seconds': [t - src['pts_seconds'][23] for t in src['pts_seconds'][23:25]],
       'h264': video_times(cpp_output), 'pipeline': video_times(vfr_output),
       'segments': {str(f.relative_to(vfr_dir)): video_times(f) for f in vfr_dir.rglob('unit*.mp4')}}

audio_dir = WORK / 'audio'
audio_dir.mkdir(exist_ok=True)
pcm = np.zeros((13 * 48000, 2), dtype='<i2')
pcm[::7680, :] = 29445
wav = audio_dir / 'click.wav'
with wave.open(str(wav), 'wb') as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(pcm.tobytes())
audio_input = audio_dir / 'source-aac.mp4'
run([FFMPEG, '-v', 'error', '-i', FIXTURES / 'source-closed_b3_id0.mp4', '-i', wav,
     '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', '9.6', audio_input])
audio_output = audio_dir / 'pipeline.mp4'
p = run([CS, '-i', audio_input, '-o', audio_output, '--keep', '0:240', '--ffmpeg-dir', SDK,
         '--work-dir', audio_dir, '--verify', '--report', audio_dir / 'pipeline.json'], required=False)
(audio_dir / 'pipeline.log').write_bytes(p.stderr)

def pcm_mono(path):
    p = run([FFMPEG, '-v', 'error', '-i', path, '-map', '0:a:0', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'])
    return np.frombuffer(p.stdout, dtype='<f4')

a, b = pcm_mono(audio_input), pcm_mono(audio_output)
threshold = float(np.max(np.abs(a))) * 0.5
def impulses(data):
    # Same threshold and grouping logic as Verifier.cs.
    found, i = [], 0
    while i < len(data):
        if abs(float(data[i])) < threshold:
            i += 1
            continue
        best, j = i, i
        while j < len(data) and abs(float(data[j])) >= threshold * 0.25:
            if abs(float(data[j])) > abs(float(data[best])):
                best = j
            j += 1
        found.append(best)
        i = j + 1
    return found
source_events, output_events = impulses(a), impulses(b)
missing = [i for i in source_events if not any(abs(i - j) <= 4 for j in output_events)]
audio = {'pipeline_exit': p.returncode, 'threshold': threshold, 'source_samples': len(a),
         'output_samples': len(b), 'source_events': source_events, 'output_events': output_events,
         'missing_near_source_samples': missing,
         'missing_windows': [{'sample': i, 'source_peak': float(np.max(np.abs(a[max(0,i-256):i+257]))),
                              'output_peak': float(np.max(np.abs(b[max(0,i-256):i+257])))} for i in missing],
         'verification': json.loads((audio_dir / 'pipeline.json').read_text(encoding='utf-8-sig')).get('verification')}
result = {'vfr': vfr, 'audio': audio}
(ROOT / 'failure-diagnostics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'vfr_expected': vfr['expected_pts_seconds'], 'vfr_h264': vfr['h264']['pts_seconds'],
                  'vfr_pipeline': vfr['pipeline']['pts_seconds'], 'audio_missing': audio['missing_windows'],
                  'audio_source_samples': len(a), 'audio_output_samples': len(b), 'threshold': threshold}, indent=2))
