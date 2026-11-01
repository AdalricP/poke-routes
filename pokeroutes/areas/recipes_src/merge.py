import json,glob,sys,os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from collections import OrderedDict
out_path='/Users/aryan/Desktop/experiments/arklarna/design/area_recipes.json'
head=json.load(open('_head.json'))
rec={}
for f in sorted(glob.glob('*.json')):
    if f.endswith('_head.json'): continue
    d=json.load(open(f))
    for k,v in d.items():
        if k in rec: print('dup',k,f)
        rec[k]=v
dex=json.load(open('/Users/aryan/Desktop/experiments/arklarna/design/pokedex.json'))
order=[s['special_area']['id'] for s in dex if s.get('special_area')]
head['recipes']={k:rec[k] for k in order if k in rec}
extra=[k for k in rec if k not in order]
if extra: print('unknown ids',extra)
json.dump(head,open(out_path,'w'),indent=1,ensure_ascii=False)
print(len(head['recipes']),'recipes; missing',len([k for k in order if k not in rec]))
