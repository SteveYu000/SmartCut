"""Compare audio/subtitle behavior with the unmodified FFmpeg executable.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from support import FF, FP
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/'build/Release/smartcut.exe'
RUN=ROOT/'research/product-validation'/('streams-'+time.strftime('%Y%m%d-%H%M%S'));RUN.mkdir(parents=True)
source=ROOT/'research/h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4'
subtitles=RUN/'captions.srt'
subtitles.write_text('1\n00:00:00,200 --> 00:00:01,400\nFirst caption\n\n2\n00:00:02,000 --> 00:00:04,500\nSecond caption\n\n3\n00:00:07,200 --> 00:00:09,000\nLast caption\n',encoding='utf-8')

def run(args,check=True):
    p=subprocess.run([str(v) for v in args],capture_output=True,timeout=180)
    if check and p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-2500:])
    return p

fixture=RUN/'source-avs.mp4'
run([FF,'-hide_banner','-v','error','-i',source,'-f','lavfi','-i','sine=frequency=997:sample_rate=48000:duration=9.6',
     '-f','lavfi','-i','sine=frequency=431:sample_rate=48000:duration=9.6','-i',subtitles,
     '-map','0:v','-map','1:a','-map','2:a','-map','3:s','-c:v','copy','-c:a','aac','-b:a','128k','-c:s','mov_text',
     '-metadata','title=Source title','-metadata:s:a:0','language=eng','-metadata:s:a:1','language=jpn','-metadata:s:s:0','language=zho',fixture])
delayed=RUN/'source-delayed-audio.mp4'
run([FF,'-hide_banner','-v','error','-i',source,'-itsoffset','20','-f','lavfi','-i','sine=frequency=300:sample_rate=48000:duration=1',
     '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac',delayed])
extra_fixtures={}
for codec,video in [('h265',ROOT/'research/h265/test-data/fixtures/source-open.mp4'),('vfr',ROOT/'research/h264/test-data/run-20261027-125923/source-vfr.mp4')]:
    path=RUN/('source-'+codec+'-avs.mp4')
    run([FF,'-hide_banner','-v','error','-i',video,'-i',fixture,'-map','0:v','-map','1:a','-map','1:s','-c','copy',path])
    extra_fixtures[codec]=path

def inspect(path):
    return json.loads(run([FP,'-v','error','-show_streams','-show_packets','-show_data_hash','sha256','-of','json',path]).stdout)

def streams(path):
    d=inspect(path);result=[]
    for stream in d['streams']:
        if stream['codec_type'] not in ('audio','subtitle'):continue
        packets=[p for p in d.get('packets',[]) if p['stream_index']==stream['index']]
        row={'type':stream['codec_type'],'codec':stream['codec_name'],'time_base':stream['time_base'],
             'sample_rate':stream.get('sample_rate'),'channels':stream.get('channels'),
             'language':stream.get('tags',{}).get('language'),'disposition':stream.get('disposition'),
             'packets':[{key:p.get(key) for key in ('pts','dts','duration','data_hash','side_data_list')} for p in packets]}
        if stream['codec_type']=='audio':
            ordinal=sum(r['type']=='audio' for r in result)
            pcm=run([FF,'-v','error','-i',path,'-map',f'0:a:{ordinal}','-f','f32le','-'])
            row['decoded_pcm_sha256']=hashlib.sha256(pcm.stdout).hexdigest();row['decoded_pcm_bytes']=len(pcm.stdout)
        result.append(row)
    return result

cases=[
 ('aac192k',fixture,['-ss','1','-to','8'],['-map','0:v:0?','-map','0:a:0?'],['-c:v:0','libx264','-preset:v:0','slower','-c:a:0','aac','-b:a:0','192k']),
 ('copy-all-aux',fixture,['-ss','1','-to','8'],['-map','0:v:0','-map','0:a','-map','0:s?'],['-c:v','libx264','-preset','slower','-c:a','copy','-c:s','copy']),
 ('absolute-map-order',fixture,['-ss','1','-to','8'],['-map','0:a:1','-map','0:s:0','-map','0:v:0','-map','0:a:0'],['-c:v:0','libx264','-preset:v:0','slower','-c:0','copy','-c:3','aac','-b:3','192k','-c:s','copy','-metadata:s:0','language=deu']),
 ('resample-mono',fixture,['-ss','0.53','-to','7.83'],['-map','0:v','-map','0:a:0'],['-c:v','libx264','-c:a','aac','-b:a','192k','-ar:a','44100','-ac:a','1']),
 ('audio-filter',fixture,['-ss','1','-t','2'],['-map','0:v','-map','0:a:0'],['-c:v','libx264','-c:a','aac','-af','volume=0.7,aresample=44100']),
 ('subtitles-encode',fixture,['-ss','0.53','-to','7.83'],['-map','0:v','-map','0:a:0','-map','0:s:0'],['-c:v','libx264','-c:a','aac','-c:s','mov_text','-metadata','title=Edited title']),
 ('two-audio-codecs',fixture,['-ss','1','-t','2'],['-map','0:v','-map','0:a'],['-c:v','libx264','-c:a:0','aac','-b:a:0','192k','-c:a:1','alac']),
 ('input-and-output-seek',fixture,['-ss','1','-t','2'],['-map','0:v','-map','0:a:0','-map','0:s?'],['-c:v','libx264','-c:a','aac','-c:s','copy']),
 ('audio-only',fixture,['-ss','1','-to','8'],['-vn','-map','0:a:0'],['-c:a','aac','-b:a','192k']),
 ('audio-only-faststart',fixture,['-ss','1','-to','8'],['-vn','-map','0:a:0'],['-c:a','aac','-b:a','192k','-movflags','+faststart']),
 ('audio-only-movie-timescale',fixture,['-ss','1','-to','8'],['-vn','-map','0:a:0'],['-c:a','aac','-b:a','192k','-movie_timescale','90000']),
 ('empty-audio-interval',delayed,['-ss','0','-to','1'],['-map','0:v','-map','0:a:0?'],['-c:v','libx264','-c:a','aac']),
 ('empty-audio-first',delayed,['-ss','0','-to','1'],['-map','0:a:0?','-map','0:v'],['-c:v','libx264','-c:a','aac']),
 ('single-frame-audio',fixture,['-ss','0.92','-to','0.96'],['-map','0:v','-map','0:a:0'],['-c:v','libx264','-c:a','aac','-b:a','192k']),
 ('hevc-audio-subtitles',extra_fixtures['h265'],['-ss','1','-to','8'],['-map','0:v','-map','0:a:0','-map','0:s:0'],['-c:v','libx265','-preset:v','slower','-x265-params','pools=4:frame-threads=2:log-level=error','-c:a','aac','-b:a','192k','-c:s','copy']),
 ('vfr-audio-subtitles',extra_fixtures['vfr'],['-ss','1.2','-to','8.64'],['-map','0:v','-map','0:a:0','-map','0:s:0'],['-c:v','libx264','-preset:v','slower','-c:a','aac','-b:a','192k','-c:s','mov_text']),
]
results={'run':str(RUN),'executable_sha256':hashlib.sha256(EXE.read_bytes()).hexdigest(),'cases':[]}
for name,input,cut,mapping,encoding in cases:
    row={'case':name};product=RUN/(name+'.mp4');control=RUN/(name+'-ffmpeg.mp4')
    before=['-ss','0.32'] if name=='input-and-output-seek' else []
    common=['-hide_banner','-loglevel','error',*before,'-i',input,*cut,*mapping,*encoding]
    try:
        p=run([EXE,*common,product],check=False);product.with_suffix('.log').write_bytes(p.stderr)
        run([FF,*common,control]);row['exit_code']=p.returncode
        if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-2000:])
        actual=streams(product);expected=streams(control)
        row.update(passed=actual==expected,actual=actual,expected=expected)
        if name=='audio-only-faststart':
            import struct
            raw=product.read_bytes();position=0;types=[]
            while position+8<=len(raw):
                size,kind=struct.unpack_from('>I4s',raw,position)
                if size==1:size=struct.unpack_from('>Q',raw,position+8)[0]
                if size==0:size=len(raw)-position
                assert size>=8
                types.append(kind);position+=size
            assert types.index(b'moov')<types.index(b'mdat')
            row['moov_before_mdat']=True
        if name=='audio-only-movie-timescale':
            import importlib.util
            spec=importlib.util.spec_from_file_location('timing_boxes',ROOT/'research/pipeline-vfr-repair/inspect_boxes.py')
            inspector=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspector)
            scale=inspector.inspect(product)['moov/mvhd']['timescale']
            assert scale==90000
            row['movie_timescale']=scale
        if not row['passed']:
            row['difference']=[{'stream':i,'fields':[k for k in set(a)|set(b) if a.get(k)!=b.get(k)]} for i,(a,b) in enumerate(zip(actual,expected)) if a!=b]
    except Exception:row.update(passed=False,error=traceback.format_exc())
    results['cases'].append(row)
    (RUN/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,row['passed'],row.get('difference'),row.get('error','')[-280:],flush=True)
(ROOT/'research/product-validation/streams-latest.json').write_text(json.dumps({'run':str(RUN),'passed':sum(r['passed'] for r in results['cases']),'total':len(cases)},indent=2),encoding='utf-8')
raise SystemExit(0 if all(r['passed'] for r in results['cases']) else 1)
