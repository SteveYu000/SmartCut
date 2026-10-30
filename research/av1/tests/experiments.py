"""Independent decoders, random access, necessity controls and input protections."""
import argparse
import shutil
import time
from common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--matrix',type=Path,required=True); args=ap.parse_args()
    matrix=json.loads((args.matrix/'results.json').read_text(encoding='utf-8')); exe=ROOT/'build/Release/av1_smart_render.exe'
    assert matrix['exe_sha256']==sha(exe)
    folder=ROOT/'test-data'/('experiments-'+time.strftime('%Y%m%d-%H%M%S')); folder.mkdir(parents=True)
    results={'exe_sha256':sha(exe),'matrix':str(args.matrix),'cases':[]}; sources={}
    def emit(row):
        results['cases'].append(row); save(folder/'results.json',results); print(row['case'],row['passed'],row.get('error','')[:240],flush=True)
    for name in ('aom','svt','rav1e','main10','vfr','ntsc','color_sar','nvenc'):
        item=next(r for r in matrix['cases'] if r['fixture']==name and r['cut']=='partial')
        path=ROOT/item['output']; s,packets=probe(path); reference=decode(path,s)
        row={'case':'libaom-decode-'+name,'input':item['output'],'input_sha256':sha(path)}
        try: row['passed']=bool(np.array_equal(reference,decode(path,s,'libaom-av1')))
        except Exception as e: row.update(passed=False,error=str(e))
        emit(row)
        if name in ('svt','rav1e','main10','vfr','nvenc'):
            keys=[i for i,p in enumerate(packets) if 'K' in p.get('flags','')]
            for i in sorted(set([0,keys[1],keys[-1]])):
                seconds=float(int(packets[i]['pts'])*Fraction(s['time_base']))
                row={'case':f'seek-{name}-{i}','input':item['output'],'input_sha256':sha(path),'sample':i,'seconds':seconds}
                try: row['passed']=bool(np.array_equal(reference[i:],decode(path,s,before=['-ss',f'{seconds:.12f}'])))
                except Exception as e: row.update(passed=False,error=str(e))
                emit(row)
        if name in ('main10','rav1e','nvenc'):
            row={'case':'nvdec-'+name,'input':item['output'],'input_sha256':sha(path)}
            try: row['passed']=bool(np.array_equal(reference,decode(path,s,'av1_cuvid')))
            except Exception as e:
                message=str(e); row.update(passed=False,error=message)
                if 'Video height 112 not within range from 128' in message: row['status']='unsupported_source_dimensions'
            emit(row)
    for name,flags,expected in [
        ('rav1e',['--experimental-single-config'],'av1C_field_mismatch'),
        ('svt',['--experimental-single-config','--experimental-drop-copy-sequence'],'decode_or_sync')]:
        path=ROOT/'test-data/fixtures'/('source-'+name+'.mp4'); src=source_data(path); out=folder/f'ablation-{name}.mp4'
        cmd=[exe,'-i',path,'-o',out,'--keep','13:227',*flags]
        p=run(cmd,check=False); out.with_suffix('.log').write_bytes(p.stderr)
        row={'case':'ablation-'+name,'input_sha256':sha(path),'command':list(map(str,cmd)),'expected_failure':expected}
        if p.returncode: row.update(passed=False,error='Export failed before the control could be validated')
        else:
            try:
                v=verify(src,out,[(13,227)]); row['validation']=v
                row['passed']=expected in v['issues'] if expected!='decode_or_sync' else 'invalid_sync_sample' in v['issues']
            except Exception as e: row.update(passed=expected=='decode_or_sync',observed_failure=str(e))
            row['output_sha256']=sha(out)
        emit(row)
    # Corrupt only the sync table: point one random-access flag at a show-existing frame.
    src=ROOT/'test-data/fixtures/source-aom.mp4'; data=bytearray(src.read_bytes()); patch=None
    def patch_stss(a,b):
        nonlocal patch
        for kind,s,e in boxes(data,a,b):
            if kind in (b'moov',b'trak',b'mdia',b'minf',b'stbl'): patch_stss(s,e)
            elif kind==b'stss':
                pos=s+12; before=bytes(data[pos:pos+4]); data[pos:pos+4]=struct.pack('>I',24)
                patch={'offset':pos,'before':before.hex(),'after':bytes(data[pos:pos+4]).hex()}
    patch_stss(0,len(data)); assert patch
    bad=folder/'false-sync.mp4'; bad.write_bytes(data)
    fixtures=[('false-sync',bad,['--keep','0:240']),
              ('changed-input-sequence',ROOT/next(r['output'] for r in matrix['cases'] if r['fixture']=='rav1e' and r['cut']=='partial'),['--keep','0:100']),
              ('out-of-range',src,['--keep','0:241']),('overlap',src,['--keep','13:40','--keep','30:60']),
              ('negative-range',src,['--keep','-1:24']),('empty-range',src,['--keep','24:24']),
              ('wrong-encoder',src,['--keep','13:227','--encoder','libx264']),
              ('non-av1',REPO/'research/h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4',['--keep','13:227'])]
    for name,path,flags in fixtures:
        out=folder/(name+'-out.mp4'); cmd=[exe,'-i',path,'-o',out,*flags]; p=run(cmd,check=False)
        row={'case':'reject-'+name,'input':str(path),'input_sha256':sha(path),'command':list(map(str,cmd)),
             'exit_code':p.returncode,'stderr':p.stderr.decode(errors='replace'), 'passed':p.returncode!=0 and not out.exists()}
        if name=='false-sync':
            row.update(case='normalize-false-sync',base_sha256=sha(src),patch=patch)
            if p.returncode==0:
                row['validation']=verify(source_data(src),out,[(0,N)]); row['passed']=row['validation']['passed']; row['output_sha256']=sha(out)
        emit(row)
    for kind in ('output','temporary','report'):
        out=folder/('protected-'+kind+'.mp4'); report=folder/('protected-'+kind+'.json')
        protected=out if kind=='output' else Path(str(out)+'.partial.mp4') if kind=='temporary' else report
        protected.write_bytes(b'preserve-existing-content'); before=sha(protected)
        cmd=[exe,'-i',src,'-o',out,'--keep','13:227','--report',report]; p=run(cmd,check=False)
        emit({'case':'protect-'+kind,'input_sha256':sha(src),'command':list(map(str,cmd)),
              'preserved_sha256':before,'exit_code':p.returncode,'passed':p.returncode!=0 and sha(protected)==before})
    chinese=folder/'中文 空格源.mp4'; shutil.copyfile(src,chinese); out=folder/'中文 空格剪切.mp4'
    cmd=[exe,'-i',chinese,'-o',out,'--keep','13:24','--keep','24:48']; p=run(cmd,check=False)
    row={'case':'unicode-and-adjacent','input_sha256':sha(chinese),'command':list(map(str,cmd))}
    if p.returncode: row.update(passed=False,error=p.stderr.decode(errors='replace'))
    else: row.update(verify(source_data(src),out,[(13,24),(24,48)]))
    emit(row)
    # NVDEC AV1 requires height >=128 on this device. Preserve the main corpus
    # and identify these additional hardware fixtures with their own recipes.
    from fixtures import AOM
    hardware_raw=rgb_frames(height=128)
    for name,pix,enc,boundary in (
        ('aom','yuv420p',AOM,'libsvtav1'),('main10','yuv420p10le',AOM,'libsvtav1'),
        ('nvenc','yuv420p',['-c:v','av1_nvenc','-preset','p4','-rc','constqp','-qp','18','-g','24'],'libaom-av1')):
        path=folder/f'hw-source-{name}.mp4'; out=folder/f'hw-cut-{name}.mp4'
        command=[FF,'-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','192x128','-framerate','25','-i','-','-an',*enc,'-pix_fmt',pix,path]
        run(command,hardware_raw)
        cut=[exe,'-i',path,'-o',out,'--keep','13:227','--encoder',boundary]; p=run(cut,check=False)
        row={'case':'nvdec-128-'+name,'source':str(path),'input_sha256':sha(path),'raw_rgb_sha256':hashlib.sha256(hardware_raw).hexdigest(),
             'generation_command':list(map(str,command)),'cut_command':list(map(str,cut))}
        try:
            if p.returncode: raise ValueError(p.stderr.decode(errors='replace'))
            row['validation']=verify(source_data(path),out,[(13,227)]); s,_=probe(out)
            row['hardware_pixels_equal']=bool(np.array_equal(decode(out,s),decode(out,s,'av1_cuvid')))
            row['passed']=row['validation']['passed'] and row['hardware_pixels_equal']
        except Exception as e: row.update(passed=False,error=str(e))
        emit(row)
    print(folder,flush=True)

if __name__=='__main__': main()
