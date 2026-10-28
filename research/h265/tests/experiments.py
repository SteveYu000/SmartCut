"""Stress, random access, hardware decode and ablation experiments.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import time
import traceback
import common as c
from common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--exe',type=Path,default=ROOT/'build/Release/smart_render.exe')
    a=ap.parse_args(); run=ROOT/'test-output'/time.strftime('experiments-%Y%m%d-%H%M%S'); run.mkdir(parents=True)
    result={'run':str(run),'cases':[],'ablations':[],'seek':[],'hardware':[],'negative':[]}
    def save(): (run/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    def export(path,label,ranges,mode='hev1',extra=()):
        out=run/(label+'.mp4'); cmd=[a.exe.resolve(),'-i',path,'-o',out,'--ps-mode',mode,*extra]
        for x,y in ranges: cmd+=['--keep',f'{x}:{y}']
        r=process(cmd,ok=False); out.with_suffix('.log').write_bytes(r.stderr)
        if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace'))
        out.with_suffix('.json').write_bytes(r.stdout)
        return out,json.loads(r.stdout)
    # Cross several 8-bit POC-LSB wraps, with gaps and leading-picture cuts.
    source=fixture(run,'open',800); src=source_data(source); ranges=[(233,489),(527,739)]
    for mode in ('hev1','stsd'):
        for bla in (True,False):
            label=f'wrap-{mode}-bla{int(bla)}'
            out,plan=export(source,label,ranges,mode,[] if bla else ['--experimental-no-bla'])
            row={'name':label,'ranges':ranges,'plan':plan,**verify(src,out,ranges,True)}
            (result['cases'] if bla else result['ablations']).append(row); save()
            print(label,row['passed'],row['issues'],flush=True)
    # No encoder runs here: both sides retain the exact same parameter sets.
    # A backwards POC-LSB jump must start a new CVS, even if MP4 PTS is correct.
    ranges=[(24,93),(312,429)]
    for mode in ('hev1','stsd'):
        for bla in (True,False):
            label=f'copy-gap-{mode}-bla{int(bla)}'
            out,plan=export(source,label,ranges,mode,[] if bla else ['--experimental-no-bla'])
            row={'name':label,'ranges':ranges,'plan':plan,**verify(src,out,ranges,True)}
            if not bla: row['failure_detected']=not row['passed']
            (result['cases'] if bla else result['ablations']).append(row); save()
            print(label,row['passed'],row['issues'],flush=True)
    # Test minimum hardware-compatible dimensions. Keep the barcode oracle.
    c.W,c.H=320,192
    gpu=run/'gpu-fixtures'; gpu.mkdir()
    source=fixture(gpu,'main10'); src=source_data(source); ranges=[(13,81),(100,227)]
    for mode in ('hev1','stsd'):
        out,plan=export(source,'gpu-main10-'+mode,ranges,mode)
        row={'name':'gpu-main10-'+mode,'plan':plan,**verify(src,out,ranges,True)}
        result['cases'].append(row)
        s,p=probe(out); soft,_=decode(out,s)
        try:
            hw,r=decode(out,s,before=['-hwaccel','cuda'])
            result['hardware'].append({'mode':mode,'frames':len(hw),'pixel_equal':bool(np.array_equal(hw,soft)),'passed':bool(np.array_equal(hw,soft))})
        except Exception as e: result['hardware'].append({'mode':mode,'passed':False,'error':str(e)})
        # Start a fresh demuxer/decoder at every MP4 sync point. Compare the
        # first returned picture with continuous decoding, never a cached frame.
        order=sorted(range(len(p)),key=lambda i:int(p[i]['pts'])); indices={i:j for j,i in enumerate(order)}
        for i,pkt in enumerate(p):
            if 'K' not in pkt['flags']: continue
            when=Fraction(int(pkt['pts']))*Fraction(s['time_base'])
            try:
                got,r=decode(out,s,before=['-ss',f'{float(when):.9f}'])
                equal=len(got)>0 and np.array_equal(got[0],soft[indices[i]])
                result['seek'].append({'mode':mode,'pts':str(when),'passed':bool(equal),'frames':len(got)})
            except Exception as e: result['seek'].append({'mode':mode,'pts':str(when),'passed':False,'error':str(e)})
        save(); print('gpu',mode,result['hardware'][-1],flush=True)
    # Remove restored source VPS/SPS/PPS only at a re-encoded -> copy transition.
    # Replace with filler of identical length so MP4 offsets/tables stay valid.
    c.W,c.H=192,112
    source=ROOT/'test-data/fixtures/source-open.mp4'
    if not source.exists():
        small=run/'small-fixtures'; small.mkdir(); source=fixture(small,'open')
    src=source_data(source)
    out,plan=export(source,'parameter-sets-reference',[(13,227)])
    raw=bytearray(out.read_bytes()); s,p=probe(out); length=(configs(out)[0][1][21]&3)+1; changed=0
    for pkt in p:
        begin=int(pkt['pos']); part=raw[begin:begin+int(pkt['size'])]
        nal_list=list(nals(part,length))
        if not any((nal[0]>>1)&63==18 for _,nal in nal_list): continue
        for offset,nal in nal_list:
            if (nal[0]>>1)&63 in (32,33,34):
                raw[begin+offset:begin+offset+len(nal)]=bytes([38<<1,1])+b'\xff'*(len(nal)-3)+b'\x80'; changed+=1
    bad=run/'ablation-missing-parameter-sets.mp4'; bad.write_bytes(raw)
    result['ablations'].append({'name':'missing-parameter-sets','replaced_nals':changed,**verify(src,bad,[(13,227)],True)})
    result['ablations'][-1]['failure_detected']=not result['ablations'][-1]['passed']
    save(); print('PS ablation',result['ablations'][-1]['issues'],flush=True)
    # CLI and structural input contracts: errors must leave no output behind.
    variants=[]
    badraw=bytearray(source.read_bytes()); ss,pp=probe(source); begin=int(pp[0]['pos']); badraw[begin:begin+4]=b'\x7f\xff\xff\xff'
    badlength=run/'negative-invalid-nal-length.mp4'; badlength.write_bytes(badraw)
    variants += [('bad_length',badlength,['--keep','0:240']),('range_oob',source,['--keep','0:241']),('range_overlap',source,['--keep','0:24','--keep','20:30']),('range_empty',source,['--keep','13:13']),('range_negative',source,['--keep','-1:3'])]
    variants += [('hybrid_input',out,['--keep','0:12'])]
    badraw=bytearray(source.read_bytes()); cfg=configs(source)[0][1]; config_start=badraw.find(cfg)
    assert config_start>=0 and cfg[23]&63==32
    badraw[config_start+24:config_start+26]=b'\x00\x02'
    badconfig=run/'negative-invalid-hvcc-array.mp4'; badconfig.write_bytes(badraw)
    variants += [('invalid_config',badconfig,['--keep','0:12'])]
    for name,path,opts in variants:
        dst=run/('negative-'+name+'.mp4'); r=process([a.exe.resolve(),'-i',path,'-o',dst,*opts],ok=False)
        result['negative'].append({'name':name,'exit_code':r.returncode,'passed':r.returncode!=0 and not dst.exists(),'log':r.stderr.decode(errors='replace')})
    # Existing output, Unicode paths and an audio track preceding the video.
    sentinel=run/'negative-existing.mp4'; sentinel.write_bytes(b'do not overwrite')
    r=process([a.exe.resolve(),'-i',source,'-o',sentinel,'--keep','0:20'],ok=False)
    result['negative'].append({'name':'existing_output','passed':r.returncode!=0 and sentinel.read_bytes()==b'do not overwrite','exit_code':r.returncode})
    unicode=run/'中文 视频.mp4'
    process([FFMPEG,'-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-i',source,'-map','0:a:0','-map','1:v:0','-c:v','copy','-c:a','aac','-t','9.6',unicode])
    out,plan=export(unicode,'中文 剪辑',[(13,227)])
    result['cases'].append({'name':'unicode_audio_first',**verify(source_data(unicode),out,[(13,227)],True)})
    save(); print(run,flush=True)
    return 0 if (all(x['passed'] for k in ('cases','seek','hardware','negative') for x in result[k])
                 and all(x.get('failure_detected',True) for x in result['ablations'])) else 1

if __name__=='__main__': raise SystemExit(main())
