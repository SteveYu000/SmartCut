"""Independent AV1/MP4 test helpers. SPDX-License-Identifier: GPL-3.0-or-later"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
from fractions import Fraction
import numpy as np
from sequence import sequence_fields, config_fields

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SDK = Path(os.environ['FFMPEG_ROOT']) if os.environ.get('FFMPEG_ROOT') else next((REPO/'research/h264/deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))
FF, FP = SDK/'bin/ffmpeg.exe', SDK/'bin/ffprobe.exe'
W, H, N = 192, 112, 240

def run(command, data=None, check=True, **kw):
    p = subprocess.run(list(map(str, command)), input=data, capture_output=True, timeout=kw.pop('timeout', 240), **kw)
    if check and p.returncode:
        raise RuntimeError(f'{command}\nexit {p.returncode}: '+p.stderr.decode(errors='replace')[-3500:])
    return p

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')

def rgb_frames(width=W,height=H,count=N):
    y, x = np.indices((height, width)); frames = []
    for i in range(count):
        im = np.stack(((x*3+y+i*5)%256, (y*5+i*3)%256, ((x^y)*2+i*7)%256), axis=2).astype(np.uint8)
        im[40:64, (i*3)%(width-25):(i*3)%(width-25)+25] = [255,30,180]
        for bit in range(10):
            im[2:18, 4+bit*16:18+bit*16] = 224 if (i>>bit)&1 else 32
        frames.append(im.tobytes())
    return b''.join(frames)

def probe(path):
    d = json.loads(run([FP,'-v','error','-select_streams','v:0','-show_streams','-show_packets','-of','json',path]).stdout)
    return d['streams'][0], d['packets']

def obus(data):
    pos = 0
    while pos < len(data):
        begin = pos; h = data[pos]; pos += 1
        if h & 0x81: raise ValueError('invalid OBU header')
        ext = data[pos] if h & 4 else 0
        if h & 4: pos += 1
        size = 0
        if h & 2:
            for k in range(8):
                v = data[pos]; pos += 1; size |= (v & 127) << (k*7)
                if not v & 128: break
            else: raise ValueError('LEB128 overflow')
        else: size = len(data)-pos
        if pos+size > len(data): raise ValueError('truncated OBU')
        yield (h>>3)&15, data[pos:pos+size], data[begin:pos+size], ext
        pos += size

def packet_info(data):
    frames=[]; sequences=[]
    for kind, payload, raw, ext in obus(data):
        if kind == 1: sequences.append(raw)
        if kind in (3,6):
            h=payload[0]; existing=bool(h & 128)
            frames.append({'existing':existing, 'type':None if existing else (h>>5)&3,
                           'show': existing or bool(h & 16)})
    return {'frames':frames,'sequences':sequences,'sync':bool(frames and not frames[0]['existing'] and frames[0]['type']==0 and frames[0]['show'] and sequences)}

def packets_bytes(path, packets):
    raw=Path(path).read_bytes()
    return [raw[int(p['pos']):int(p['pos'])+int(p['size'])] for p in packets]

def decode(path, stream, decoder='libdav1d', before=()):
    pix='yuv420p10le' if '10' in stream['pix_fmt'] else 'yuv420p'
    # Keep setpts' frame counter across format/color changes. FFmpeg's default
    # filter reinitialization can reset N and cause a false raw-muxer DTS error.
    # Original display timestamps and configuration changes are audited separately.
    p=run([FF,'-v','error','-xerror','-err_detect','explode','-c:v',decoder,'-reinit_filter','0',*before,'-i',path,
           '-map','0:v:0','-vf','setpts=N/25/TB','-enc_time_base','1/25','-fps_mode','passthrough',
           '-pix_fmt',pix,'-f','rawvideo','-'],check=False)
    if p.returncode or p.stderr.strip(): raise ValueError('strict decode: '+p.stderr.decode(errors='replace')[-1500:])
    a=np.frombuffer(p.stdout, dtype=np.uint16 if '10' in pix else np.uint8)
    return a.reshape((-1,int(stream['width'])*int(stream['height'])*3//2))

def boxes(data, begin=0, end=None):
    end=len(data) if end is None else end
    while begin+8<=end:
        size,kind=struct.unpack_from('>I4s',data,begin); header=8
        if size==1: size=struct.unpack_from('>Q',data,begin+8)[0]; header=16
        if size==0: size=end-begin
        if size<header or begin+size>end: raise ValueError('invalid MP4 box')
        yield kind,begin+header,begin+size
        begin+=size

def sample_tables(path,details=False):
    data=Path(path).read_bytes(); tables={}; configs=[]; descriptions=[]
    def descend(a,b):
        for kind,s,e in boxes(data,a,b):
            if kind in (b'moov',b'trak',b'mdia',b'minf',b'stbl'): descend(s,e)
            elif kind==b'stsd':
                for tag,v,z in boxes(data,s+8,e):
                    if tag==b'av01':
                        desc=dict(zip(('width','height'),struct.unpack_from('>HH',data,v+24)))
                        for child,k,l in boxes(data,v+78,z):
                            if child==b'av1C': configs.append(data[k:l])
                            elif child==b'colr' and data[k:k+4]==b'nclx':
                                desc['colr']=dict(zip(('color_primaries','transfer_characteristics','matrix_coefficients'),struct.unpack_from('>HHH',data,k+4)))
                                desc['colr']['color_range']=data[k+10]>>7
                        descriptions.append(desc)
            elif kind in (b'stsc',b'stco',b'co64',b'stsz',b'ctts'): tables[kind]=data[s:e]
    descend(0,len(data))
    mapping=[]
    if b'stsc' in tables:
        d=tables[b'stsc']; entries=[struct.unpack_from('>III',d,8+i*12) for i in range(struct.unpack_from('>I',d,4)[0])]
        chunks=tables.get(b'stco',tables.get(b'co64',b''))
        count=struct.unpack_from('>I',chunks,4)[0] if chunks else 0
        for i,(first,n,desc) in enumerate(entries):
            last=entries[i+1][0] if i+1<len(entries) else count+1
            mapping.extend([desc-1]*((last-first)*n))
    result=(configs,mapping,b'ctts' in tables)
    return (*result,descriptions) if details else result

def source_data(path):
    s,p=probe(path); raw=packets_bytes(path,p); frames=decode(path,s)
    times=[int(x['pts']) for x in p]; times.append(times[-1]+int(p[-1]['duration']))
    assert len(frames)==len(p)==N
    assert all(a<b for a,b in zip(times,times[1:]))
    return {'path':str(path),'sha256':sha(path),'stream':s,'packets':p,'raw':raw,'frames':frames,'times':times,
            'infos':[packet_info(x) for x in raw]}

def verify(source, output, ranges):
    s,p=probe(output); decoded=decode(output,s); wanted=[i for a,b in ranges for i in range(a,b)]
    st=Fraction(source['stream']['time_base']); ot=Fraction(s['time_base']); t=source['times']
    times=[]; offset=Fraction(0)
    for a,b in ranges:
        times.extend(offset+(t[i]-t[a])*st for i in range(a,b)); offset+=(t[b]-t[a])*st
    issues=[]
    for key in ('sample_aspect_ratio','color_range','color_space','color_transfer','color_primaries'):
        expected=source['stream'].get(key)
        if expected not in (None,'unknown','unspecified','0:1') and s.get(key)!=expected: issues.append('metadata_'+key)
    if len(p)!=len(wanted) or len(decoded)!=len(wanted): issues.append('frame_count')
    actual=[int(x['pts'])*ot for x in p]
    if actual!=times: issues.append('packet_pts')
    df=json.loads(run([FP,'-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=pts','-of','json',output]).stdout)['frames']
    if [int(x['pts'])*ot for x in df]!=times: issues.append('decoded_pts')
    if any(x.get('dts')!=x.get('pts') for x in p): issues.append('av1_composition_offset')
    duration=(int(p[-1]['pts'])+int(p[-1]['duration']))*ot if p else Fraction(0)
    if duration!=offset: issues.append('duration')
    keys=[i for i,x in enumerate(source['infos']) if x['sync']]+[len(source['packets'])]
    expected_copy=set()
    for a,b in ranges:
        for start,end in zip(keys,keys[1:]):
            if start>=a and end<=b: expected_copy.update(range(start,end))
    raw=packets_bytes(output,p); same=[]; confirmed=[]; pixel_bad=[]; barcode_bad=[]; psnrs=[]
    peak=1023 if '10' in s['pix_fmt'] else 255
    for out_i,src_i in enumerate(wanted[:min(len(p),len(decoded))]):
        equal=raw[out_i]==source['raw'][src_i]
        if equal: same.append(out_i)
        original=source['frames'][src_i]; got=decoded[out_i]
        # show_existing_frame can be the same two-byte command after a new
        # encode. Byte equality there does not prove that pixels were copied.
        if src_i in expected_copy and equal:
            confirmed.append(out_i)
            if not np.array_equal(original,got): pixel_bad.append(out_i)
        mse=np.mean((original.astype(np.float64)-got.astype(np.float64))**2)
        if mse: psnrs.append(float(10*np.log10(peak**2/mse)))
        w,h=int(s['width']),int(s['height']); y=got[:w*h].reshape(h,w)/peak
        code=sum((np.mean(y[5:15,7+i*16:15+i*16])>.5)<<i for i in range(10))
        if code!=src_i: barcode_bad.append([out_i,src_i,int(code)])
    if pixel_bad: issues.append('copied_pixels')
    if barcode_bad: issues.append('display_frame_identity')
    if psnrs and min(psnrs)<30: issues.append('quality_below_30db')
    configs,mapping,ctts,descriptions=sample_tables(output,details=True)
    if ctts: issues.append('forbidden_ctts')
    sync_errors=[]; config_mismatches=[]; contract_errors=[]; description_errors=[]
    for i,(data,packet) in enumerate(zip(raw,p)):
        inf=packet_info(data)
        if 'K' in packet.get('flags','') and not inf['sync']: sync_errors.append(i)
        if i<len(mapping) and mapping[i]<len(configs):
            config=configs[mapping[i]]
            # Exact SH equality is a deliberately stronger diagnostic than the
            # binding's required av1C field agreement; reported separately.
            advertised=[x[2] for x in obus(config[4:]) if x[0]==1]
            if advertised and inf['sequences'] and advertised[-1]!=inf['sequences'][-1]: config_mismatches.append(i)
            for sequence in inf['sequences']:
                fields=sequence_fields(next(obus(sequence))[1]); record=config_fields(config)
                different={k:[value,fields[k]] for k,value in record.items() if value!=fields[k]}
                if different: contract_errors.append({'sample':i,'description':mapping[i]+1,'fields':different})
                desc=descriptions[mapping[i]]; expected={k:desc[k] for k in ('width','height')}; expected.update(desc.get('colr',{}))
                different={k:[value,fields[k]] for k,value in expected.items() if value!=fields[k]}
                if different: description_errors.append({'sample':i,'description':mapping[i]+1,'fields':different})
    if sync_errors: issues.append('invalid_sync_sample')
    if contract_errors: issues.append('av1C_field_mismatch')
    if description_errors: issues.append('sample_entry_field_mismatch')
    return {'passed':not issues,'issues':issues,'expected_frames':len(wanted),'decoded_frames':len(decoded),
            'exact_packets_at_expected_positions':len(same),'expected_copy_frames':len(expected_copy),
            'confirmed_copy_frames':len(confirmed),'copy_efficiency_passed':len(confirmed)>=len(expected_copy),'copied_pixel_mismatches':pixel_bad,
            'barcode_errors':barcode_bad[:12],'min_psnr_db':min(psnrs) if psnrs else None,
            'duration_seconds':float(duration),'expected_duration_seconds':float(offset),
            'sample_descriptions':len(configs),'config_sequence_differences':config_mismatches,
            'av1C_field_errors':contract_errors,
            'sample_entry_errors':description_errors,
            'sync_errors':sync_errors,'output_sha256':sha(output)}
