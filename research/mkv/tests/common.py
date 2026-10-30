"""MKV oracle: demuxed payload, actual decoded display order, EBML and rational PTS.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from fractions import Fraction
import numpy as np
import ebml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SDK = Path(os.environ.get('FFMPEG_ROOT', str(REPO/'deps/ffmpeg-9.0.2-full_build-shared')))
FF, FP = SDK/'bin/ffmpeg.exe', SDK/'bin/ffprobe.exe'
EXE = ROOT/'build/Release/mkv-smartcut.exe'
sys.path.insert(0, str(REPO/'research/av1/tests'))
from sequence import sequence_fields, config_fields


def run(args, check=True, **kw):
    p = subprocess.run(list(map(str,args)), capture_output=True, timeout=kw.pop('timeout',180), **kw)
    if check and p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-2500:])
    return p


def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unhex(text):
    return b''.join(bytes.fromhex(line.split(':',1)[1].split('  ')[0].replace(' ',''))
                    for line in text.splitlines() if ':' in line)


def probe(path, data=True, frames=False):
    args=[FP,'-v','error','-select_streams','v:0','-show_streams','-show_packets','-show_format']
    if data: args+=['-show_data']
    if frames: args+=['-show_frames']
    out=json.loads(run(args+['-of','json',path]).stdout)
    if frames:
        out['packets']=[x for x in out.get('packets_and_frames',[]) if x['type']=='packet']
        out['frames']=[x for x in out.get('packets_and_frames',[]) if x['type']=='frame']
    if data:
        for p in out['packets']: p['raw']=unhex(p.pop('data',''))
        for s in out['streams']: s['extra']=unhex(s.pop('extradata',''))
    return out


def obus(data):
    pos=0
    while pos<len(data):
        start=pos; h=data[pos]; pos+=1
        if h&0x81: raise ValueError('invalid OBU')
        if h&4: pos+=1
        n=len(data)-pos
        if h&2:
            n=0
            for i in range(8):
                v=data[pos]; pos+=1; n|=(v&127)<<(7*i)
                if not v&128: break
        if pos+n>len(data): raise ValueError('truncated OBU')
        yield (h>>3)&15,data[pos:pos+n],data[start:pos+n]
        pos+=n


def picture(data, stream, normalize_cra=True):
    if stream['codec_name']=='av1':
        # TD/SH/metadata may be injected, while encoded picture OBUs stay opaque.
        return b''.join(raw for typ,_,raw in obus(data) if typ in (3,4,6,7))
    extra=stream['extra']; avc=stream['codec_name']=='h264'
    length=(extra[4 if avc else 21]&3)+1
    pos=0; result=[]
    while pos<len(data):
        n=int.from_bytes(data[pos:pos+length],'big'); pos+=length
        if n<=0 or pos+n>len(data): raise ValueError('invalid length-prefixed NAL')
        nal=data[pos:pos+n]; pos+=n; typ=nal[0]&31 if avc else (nal[0]>>1)&63
        if (avc and typ in (1,5)) or (not avc and typ<32):
            if not avc and normalize_cra and typ in (18,21):
                nal=bytes([(nal[0]&0x81)|(21<<1)])+nal[1:]
            result.append(len(nal).to_bytes(4,'big')+nal)
    return b''.join(result)


def decode(path, stream, before=()):
    pix='yuv420p10le' if '10' in stream['pix_fmt'] else 'yuv420p'
    args=[FF,'-v','error','-xerror','-err_detect','explode','-reinit_filter','0']
    p=run(args+list(before)+['-i',path,'-map','0:v:0','-vf','setpts=N/25/TB','-enc_time_base','1/25',
          '-fps_mode','passthrough','-pix_fmt',pix,'-f','rawvideo','-'],check=False)
    if p.returncode or p.stderr.strip(): raise ValueError('strict decode: '+p.stderr.decode(errors='replace')[-1600:])
    return np.frombuffer(p.stdout,dtype=np.uint16 if '10' in pix else np.uint8).reshape(
        (-1,int(stream['width'])*int(stream['height'])*3//2))


def source(path):
    d=probe(path); s=d['streams'][0]; packets=d['packets']
    order=sorted(range(len(packets)),key=lambda i:int(packets[i]['pts']))
    pts=[int(packets[i]['pts'])*Fraction(s['time_base']) for i in order]
    pts.append(pts[-1]+int(packets[order[-1]]['duration'])*Fraction(s['time_base']))
    return dict(path=path,stream=s,packets=packets,order=order,times=pts,frames=decode(path,s),sha256=sha(path))


def verify(src, path, ranges, copy_indices=None, seek=False):
    issues=[]; out=probe(path,frames=True); s=out['streams'][0]; packets=out['packets']; tb=Fraction(s['time_base'])
    wanted=[]; expected=[]; durations=[]; offset=Fraction(0)
    for a,b in ranges:
        wanted.extend(range(a,b))
        expected.extend(offset+t-src['times'][a] for t in src['times'][a:b])
        durations.extend(src['times'][i+1]-src['times'][i] for i in range(a,b))
        offset+=src['times'][b]-src['times'][a]
    order=sorted(range(len(packets)),key=lambda i:int(packets[i]['pts']))
    observed=[int(packets[i]['pts'])*tb for i in order]
    tolerance=Fraction(1,2000)
    errors=[abs(a-b) for a,b in zip(observed,expected)]
    if len(packets)!=len(wanted): issues.append('packet_count')
    if len(observed)!=len(expected) or any(e>tolerance for e in errors): issues.append('display_pts')
    decoded_pts=[int(f['best_effort_timestamp'])*tb for f in out['frames']]
    if len(decoded_pts)!=len(expected) or any(abs(a-b)>tolerance for a,b in zip(decoded_pts,expected)):
        issues.append('decoded_display_pts')
    frame_error=[]; pixel_error=[]; exact=[]; quality=[]
    try:
        frames=decode(path,s)
        if len(frames)!=len(wanted): issues.append('decoded_count')
        peak=1023 if '10' in s['pix_fmt'] else 255
        w,h=int(s['width']),int(s['height'])
        for n,k in enumerate(wanted[:len(frames)]):
            f=frames[n]; original=src['frames'][k]
            code=sum((int(f[:w*h].reshape(h,w)[6:14,8+bit*16:14+bit*16].mean()>peak/2)<<bit) for bit in range(10))
            if code!=k: frame_error.append([n,k,code])
            if n<len(order):
                same=picture(packets[order[n]]['raw'],s)==picture(src['packets'][src['order'][k]]['raw'],src['stream'])
                if same and np.array_equal(f,original): exact.append(k)
                if copy_indices is not None and k in copy_indices and (not same or not np.array_equal(f,original)):
                    pixel_error.append([n,k])
            mse=np.mean((f.astype(np.float64)-original.astype(np.float64))**2)
            if mse: quality.append(float(10*np.log10(peak*peak/mse)))
        if frame_error: issues.append('frame_identity')
        if pixel_error: issues.append('copy_payload_or_pixels')
        if quality and min(quality)<30: issues.append('quality_below_30db')
        decoded_count=len(frames)
    except Exception as e:
        issues.append('strict_decode'); decoded_count=None; frame_error=[str(e)]
    for key in ('width','height','pix_fmt','sample_aspect_ratio','color_range','color_space','color_transfer','color_primaries'):
        if s.get(key)!=src['stream'].get(key): issues.append('metadata_'+key)
    raw=ebml.audit(path); block_by_pts={x['pts']*Fraction(raw['timestamp_scale_ns'],10**9):x for x in raw['blocks'] if x['track']==1}
    actual_duration=Fraction(str(raw.get('duration_ticks',0)))*Fraction(raw['timestamp_scale_ns'],10**9)
    if abs(actual_duration-offset)>Fraction(1,1000): issues.append('segment_duration')
    duration_errors=[]; inferred_durations=0
    default=Fraction(raw['tracks'][0].get('default_duration_ns',0),10**9)
    for n,t in enumerate(observed):
        if n>=len(durations): break
        block=block_by_pts.get(t)
        if block:
            if block['duration'] is None and not default:
                # Absence is not a declaration of zero duration. A reader can
                # infer the hold interval from the next presentation timestamp.
                # For the last image the Segment end is the only available bound.
                duration=(observed[n+1] if n+1<len(observed) else actual_duration)-t
                inferred_durations+=1
            else:
                duration=default if block['duration'] is None else block['duration']*Fraction(raw['timestamp_scale_ns'],10**9)
            if abs(duration-durations[n])>Fraction(1,1000): duration_errors.append([n,float(duration),float(durations[n])])
    if duration_errors: issues.append('block_duration')
    sequences=set(); av1_contract=[]
    if s['codec_name']=='av1':
        extra=raw['tracks'][0].get('codec_private',b'')
        for p in packets:
            for typ,payload,body in obus(p['raw']):
                if typ==1:
                    sequences.add(body)
                    fields=sequence_fields(payload)
                    mismatch={k:[v,fields[k]] for k,v in config_fields(extra).items() if v!=fields[k]}
                    if mismatch: av1_contract.append(mismatch)
        if len(sequences)>1: issues.append('av1_multiple_sequence_headers')
        if av1_contract: issues.append('av1_codec_private_fields')
    seeks=[]
    if seek and decoded_count:
        # Fresh process/decoder for each seek; pixel equality with linear decode.
        for n in sorted(set([min(6,len(wanted)-1),len(wanted)//2,max(0,len(wanted)-8)])):
            t=observed[n]
            try:
                got=decode(path,s,before=['-ss',f'{float(t):.9f}'])
                ok=len(got)>0 and np.array_equal(got[0],frames[n])
                seeks.append({'index':n,'time':float(t),'passed':ok})
            except Exception as e: seeks.append({'index':n,'time':float(t),'passed':False,'error':str(e)})
        if not all(x['passed'] for x in seeks): issues.append('seek')
    return dict(passed=not issues,issues=sorted(set(issues)),expected_frames=len(wanted),decoded_frames=decoded_count,
                exact_picture_and_pixels=len(exact),expected_copy_frames=len(copy_indices) if copy_indices is not None else None,
                max_pts_error_ms=float(max(errors,default=0)*1000),duration=float(actual_duration),expected_duration=float(offset),
                min_psnr_db=min(quality) if quality else None,frame_errors=frame_error[:5],copy_errors=pixel_error[:5],
                block_duration_errors=duration_errors[:8],inferred_block_durations=inferred_durations,
                av1_sequence_count=len(sequences),av1_config_errors=av1_contract[:5],
                seeks=seeks,ebml=ebml.summary(raw),output_sha256=sha(path))
