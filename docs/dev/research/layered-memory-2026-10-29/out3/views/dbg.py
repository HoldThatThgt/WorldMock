import json
from common import *
from model import Run
from part1_sizes import persona_from_request, render
R=Run("V")
first=next(d for d in R.decisions if d["fresh"]); base=persona_from_request(first)
for d in R.decisions[100:]:
    if d["fresh"]:
        a=compact(render(base,d["pv"])); b=compact(persona_from_request(d))
        if a!=b:
            i=next(i for i in range(min(len(a),len(b))) if a[i]!=b[i])
            print(len(a),len(b)); print(a[i-200:i+200]); print(b[i-200:i+200]); break
