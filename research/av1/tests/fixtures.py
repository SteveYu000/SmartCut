"""Generate reproducible inputs and record their exact identities (no media in Git)."""
import argparse
from common import *

AOM=['-c:v','libaom-av1','-cpu-used','6','-crf','18','-b:v','0','-g','24','-lag-in-frames','16','-auto-alt-ref','1','-row-mt','1','-threads','4']
SPECS={
 'aom':('yuv420p','25',AOM,[]),
 'svt':('yuv420p','25',['-c:v','libsvtav1','-preset','8','-crf','18','-g','24','-svtav1-params','lp=4:scd=0'],[]),
 'rav1e':('yuv420p','25',['-c:v','librav1e','-speed','8','-qp','60','-g','24','-threads','4'],[]),
 'main10':('yuv420p10le','25',AOM,[]),
 'vfr':('yuv420p','25',AOM,['-vf','setpts=(N+floor(N/3))/(25*TB)','-fps_mode','vfr']),
 'ntsc':('yuv420p','30000/1001',AOM,[]),
 'offset':('yuv420p','25',AOM,['-output_ts_offset','2']),
 'fragmented':('yuv420p','25',AOM,['-movflags','frag_keyframe+empty_moov+default_base_moof']),
 'color_sar':('yuv420p','25',AOM,['-vf','setsar=4/3','-color_range','pc','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709']),
 'tiles':('yuv420p','25',AOM,['-tiles','2x1']),
 'nvenc':('yuv420p','25',['-c:v','av1_nvenc','-preset','p4','-rc','constqp','-qp','18','-g','24'],[]),
}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--names',default=','.join(SPECS)); args=ap.parse_args()
    folder=ROOT/'test-data/fixtures'; folder.mkdir(parents=True,exist_ok=True)
    raw=rgb_frames(); manifest_path=ROOT/'evidence/fixtures.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'ffmpeg_version':run([FF,'-version']).stdout.decode(),'raw_rgb_sha256':hashlib.sha256(raw).hexdigest(),'fixtures':{}}
    for name in args.names.split(','):
        pix,rate,enc,extra=SPECS[name]; path=folder/('source-'+name+'.mp4')
        cmd=[FF,'-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{W}x{H}','-framerate',rate,'-i','-','-an',*enc,'-pix_fmt',pix,*extra,path]
        if not path.exists():
            p=run(cmd,raw,check=False); (folder/(name+'.generation.log')).write_bytes(p.stderr)
            if p.returncode: raise RuntimeError(name+': '+p.stderr.decode(errors='replace')[-2000:])
        s,p=probe(path); infos=[packet_info(x) for x in packets_bytes(path,p)]
        manifest['fixtures'][name]={'path':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,'sha256':sha(path),
           'command':['<FFMPEG>' if x==FF else str(x.relative_to(ROOT)) if isinstance(x,Path) and x.is_relative_to(ROOT) else str(x) for x in cmd],
           'width':s['width'],'height':s['height'],'pix_fmt':s['pix_fmt'],'time_base':s['time_base'],'packets':len(p),
           'sync_indices':[i for i,x in enumerate(infos) if x['sync']],
           'hidden_frames':sum(not f['show'] for x in infos for f in x['frames']),
           'show_existing_frames':sum(f['existing'] for x in infos for f in x['frames'])}
        save(manifest_path,manifest)
        print(name,len(p),manifest['fixtures'][name]['hidden_frames'],manifest['fixtures'][name]['show_existing_frames'],flush=True)

if __name__=='__main__': main()
