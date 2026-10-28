import json, urllib.request, pathlib, urllib.parse, sys
ROOT = pathlib.Path(__file__).resolve().parent / 'evidence'
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'SmartRendering-Source-Research','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=45) as r: return json.load(r)
def save(name,data):
    (ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
jobs={
 'deep_issues':'repos/DeepRegular/SmartCut/issues?state=all&per_page=100',
 'python_issues':'repos/skeskinen/smartcut/issues?state=all&per_page=100',
 'avcut_issues':'repos/anyc/avcut/issues?state=all&per_page=100',
 'mediamolder_issues':'repos/MediaMolder/mediamolder/issues?state=all&per_page=100',
 'vid_smart_issues':'search/issues?q='+urllib.parse.quote('repo:ozmartian/vidcutter is:issue smart')+'&per_page=100',
 'lossless_smart_issues':'search/issues?q='+urllib.parse.quote('repo:mifi/lossless-cut is:issue "smart cut"')+'&per_page=100',
}
for name,route in jobs.items():
    try:
        data=get('https://api.github.com/'+route);save(name+'.json',data)
        items=data.get('items',[]) if isinstance(data,dict) else data
        print(name, len(items), flush=True)
    except Exception as e: print(name,str(e),flush=True)
