import json
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parent
ff=root.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
source=root.parent/'h264/test-data/run-20261027-125923/source-vfr.mp4'
folder=root/'bsf-experiments';folder.mkdir(exist_ok=True)

def run(cmd):
    p=subprocess.run([str(x) for x in cmd],capture_output=True)
    if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1500:])
    return p.stdout

def probe(path):
    d=json.loads(run([ff/'ffprobe.exe','-v','error','-show_entries','stream=duration,time_base:packet=pts,dts,duration','-of','json',path]))
    return {'count':len(d['packets']),'first':d['packets'][:4],'last':d['packets'][-2:],'stream':d['streams'][0]}

tests={
 'plain':[],
 'duration-only':['-bsf:v',"setts=duration='if(eq(N,47),1024,DURATION)'"],
 'prescale':['-bsf:v',"setts=time_base=1/12800:prescale=1:duration='if(eq(N,47),1024,DURATION)'"],
 'explicit-pts-dts':['-bsf:v',"setts=pts=PTS:dts=DTS:duration='if(eq(N,47),1024,DURATION)'"],
 'last-display-duration':['-bsf:v',"setts=pts=PTS:dts=DTS:duration='if(eq(PTS-STARTPTS,31744),1024,DURATION)'"],
 'all-duration':['-bsf:v',"setts=pts=PTS:dts=DTS:duration=1024"],
 'last-duration-1536':['-bsf:v',"setts=pts=PTS:dts=DTS:duration='if(eq(N,47),1536,DURATION)'"],
}
result={}
for name,more in tests.items():
    out=folder/(name+'.mp4')
    if not out.exists(): run([ff/'ffmpeg.exe','-v','error','-ss','0','-i',source,'-frames:v','48','-c','copy',*more,'-an',out])
    result[name]=probe(out)
remux=folder/'remux.mp4'
if not remux.exists():run([ff/'ffmpeg.exe','-v','error','-i',folder/'plain.mp4','-c','copy','-bsf:v',"setts=duration='if(eq(N,47),1024,DURATION)'",remux])
result['remux']=probe(remux)
(root/'copy-bsf-experiments.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
