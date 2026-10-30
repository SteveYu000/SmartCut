"""Read-only instrumentation of one upstream failure; no packet behavior changes.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
from pathlib import Path
import runpy
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1];REPO=ROOT.parents[1]
sys.path.insert(0,str(REPO/'research/h265/deps/python'))
sys.path.insert(0,str(REPO/'sources/skeskinen-smartcut'))
import av
from smartcut.video_cutter import VideoCutter

folder=ROOT/'test-data/trace-skeskinen';folder.mkdir(parents=True,exist_ok=True)
records=[];original=VideoCutter._fix_packet_timestamps
def trace(self,packet):
    before=dict(pts=packet.pts,dts=packet.dts,duration=packet.duration,time_base=str(packet.time_base))
    original(self,packet)
    after=dict(pts=packet.pts,dts=packet.dts,duration=packet.duration,time_base=str(packet.time_base))
    records.append(dict(before=before,after=after))
VideoCutter._fix_packet_timestamps=trace
sys.argv=['smartcut',str(ROOT/'test-data/fixtures/hevc-open.mkv'),str(folder/'output.mkv'),
          '--frames','--keep','13,226']
error=None
with av.logging.Capture(local=False) as logs:
    av.logging.set_level(av.logging.DEBUG)
    try:runpy.run_module('smartcut',run_name='__main__')
    except Exception:error=traceback.format_exc()
data={'pyav_version':av.__version__,'libraries':av.library_versions,'argv':sys.argv,
      'error':error,'packets':records,'ffmpeg_log':logs}
(folder/'results.json').write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(av.__version__,av.library_versions)
print('PTS<DTS after repair:',[p for p in records if p['after']['pts'] is not None and p['after']['dts'] is not None and p['after']['pts']<p['after']['dts']][:3])
print('error:',error.splitlines()[-1] if error else None)
print([x for x in logs if x[0]<=av.logging.ERROR][-5:])
