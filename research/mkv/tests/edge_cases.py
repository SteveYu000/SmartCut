"""Explicit progressive metadata and unrepresentable sub-ms schedule checks."""
import time
from common import *
from matrix import ENCODERS

folder=ROOT/'test-data'/('edges-'+time.strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
mkv=REPO/'sources/mkvtoolnix-103.0-win64/mkvtoolnix/mkvmerge.exe'
result={'prototype_sha256':sha(EXE),'cases':[]}
src=ROOT/'test-data/fixtures/hevc-open.mkv';z=folder/'hevc-zlib-progressive.mkv'
cmd=[mkv,'-o',z,'--compression','0:zlib','--field-order','0:0',src]
run(cmd); out=folder/'hevc-zlib-cut.mkv'
cut=[EXE,'-v','error','-y','-i',z,'--keep-frames','13:227',*ENCODERS['hevc'],out]
p=run(cut,check=False)
row={'name':'hevc-zlib-explicit-progressive','fixture_command':list(map(str,cmd)),
     'input_sha256':sha(z),'command':list(map(str,cut)),'exit_code':p.returncode}
row.update(verify(source(z),out,[(13,227)],seek=True) if p.returncode==0 else
           dict(passed=False,error=p.stderr.decode(errors='replace')))
result['cases'].append(row);print(row['name'],row['passed'],flush=True)

high=folder/'high-rate.mp4'
cmd=[FF,'-v','error','-y','-f','lavfi','-i','testsrc2=size=192x128:rate=2000:duration=0.04',
     '-c:v','libx264','-g','20','-bf','0','-pix_fmt','yuv420p',high]
run(cmd);micro=folder/'high-rate.mkv'
remux=[mkv,'-o',micro,'--timestamp-scale','1000',high];run(remux)
out=folder/'high-rate-out.mkv'
cut=[EXE,'-v','error','-y','-i',micro,'--keep-frames','0:80','-c:v','copy',out]
p=run(cut,check=False)
row={'name':'reject-unrepresentable-millisecond-grid','fixture_command':list(map(str,cmd)),
     'remux_command':list(map(str,remux)),'input_sha256':sha(micro),'command':list(map(str,cut)),
     'exit_code':p.returncode,'error':p.stderr.decode(errors='replace'),
     'passed':p.returncode!=0 and not out.exists() and b'Millisecond Matroska time base' in p.stderr}
result['cases'].append(row);print(row['name'],row['passed'],row['error'],flush=True)
save(folder/'results.json',result);save(ROOT/'local/edges-latest.json',{'path':str(folder/'results.json')})
