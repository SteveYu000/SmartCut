import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SDK=ROOT.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
OLD=ROOT.parent/'h264-pipeline-comparison/diagnostics/vfr/run-20261028-105359-4776568'

def run(args):
    p=subprocess.run([str(x) for x in args],capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode(errors='replace')[-1500:])
    return p.stdout

def inspect(path):
    return json.loads(run([SDK/'ffprobe.exe','-v','error','-show_entries','stream=time_base,start_time,duration:packet=pts,dts,duration','-of','json',path]))

concat=ROOT/'planned.ffconcat'
concat.write_text("ffconcat version 1.0\nfile '"+(OLD/'unit000.mp4').as_posix()+"'\nduration 0.080000\nfile '"+(OLD/'unit001.mp4').as_posix()+"'\nduration 0.040000\n",encoding='utf-8')
joined=ROOT/'explicit-duration.mp4'
run([SDK/'ffmpeg.exe','-v','error','-f','concat','-safe','0','-i',concat,'-c','copy','-tag:v','avc3',joined])
last=ROOT/'single-long-frame.mp4'
run([SDK/'ffmpeg.exe','-v','error','-i',OLD/'unit000.mp4','-c','copy','-bsf:v',"setts=duration='if(eq(N,0),1024,DURATION)'",last])
result={'explicit_duration_concat':inspect(joined),'setts_single_frame':inspect(last)}
(ROOT/'initial-experiment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
