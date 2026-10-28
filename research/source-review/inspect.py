import json,pathlib,sys
sys.stdout.reconfigure(encoding='utf-8')
EVIDENCE=pathlib.Path(__file__).resolve().parent/'evidence'
cmd=sys.argv[1]
if cmd=='issues':
 for name in sys.argv[2:]:
  data=json.loads((EVIDENCE/(name+'_issues.json')).read_text(encoding='utf-8'))
  rows=data.get('items',[]) if isinstance(data,dict) else data
  for r in rows: print(name,r['number'],r['state'],r.get('state_reason'),r['title'])
elif cmd=='issue':
 name=sys.argv[2]; nums=set(map(int,sys.argv[3:]))
 data=json.loads((EVIDENCE/(name+'_issues.json')).read_text(encoding='utf-8'))
 for r in data.get('items',[]) if isinstance(data,dict) else data:
  if r['number'] in nums: print(json.dumps({k:r.get(k) for k in ['number','title','state','state_reason','updated_at','closed_at','body','comments','html_url']},ensure_ascii=False,indent=2))
elif cmd=='lines':
 path=pathlib.Path(sys.argv[2]); lines=path.read_text(encoding='utf-8',errors='replace').splitlines()
 a=int(sys.argv[3]) if len(sys.argv)>3 else 1
 b=int(sys.argv[4]) if len(sys.argv)>4 else len(lines)
 for i in range(a-1,min(b,len(lines))): print(f'{i+1}: {lines[i]}')
elif cmd=='comments':
 for name in sys.argv[2:]:
  p=EVIDENCE/(name+'_comments.json')
  print('\nFILE',name)
  for r in json.loads(p.read_text(encoding='utf-8')):
   print(r['user']['login'],r['updated_at'],r['html_url']); print(r['body'])
