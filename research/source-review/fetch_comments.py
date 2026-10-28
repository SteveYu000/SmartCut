from fetch_evidence import get,save
jobs={'skeskinen/smartcut':[23,24,27,28], 'mifi/lossless-cut':[2494,2050,2049,2013,2912,2615], 'ozmartian/vidcutter':[114,258,175,402,368], 'anyc/avcut':[9,19,21,25,27], 'DeepRegular/SmartCut':[8,9]}
for repo,nums in jobs.items():
 for n in nums:
  name=repo.replace('/','_')+f'_{n}_comments.json'
  try:
   data=get(f'https://api.github.com/repos/{repo}/issues/{n}/comments?per_page=100')
   save(name,data);print(name,len(data),flush=True)
  except Exception as e: print(name,str(e),flush=True)
for n in [56,77,79]:
 try:
  data=get(f'https://api.github.com/repos/MediaMolder/mediamolder/pulls/{n}')
  save(f'mediamolder_PR{n}.json',data);print('PR',n,data.get('merged_at'),flush=True)
 except Exception as e: print(n,str(e),flush=True)
