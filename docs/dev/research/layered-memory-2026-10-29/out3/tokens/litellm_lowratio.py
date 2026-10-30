"""List requests where litellm/DeepSeek ratio is lowest, by family (diagnostic for litellm_check)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from litellm_check import load, family  # noqa: E402
from worldmock.agents.prompt.prompt_capacity import prompt_token_count  # noqa: E402
from collections import defaultdict
import numpy as np
rows = load("V") + load("B")
fam = defaultdict(list)
for run, cid, req, act in rows:
    fam[family(cid, req)].append(prompt_token_count(req) / act)
for k, v in fam.items():
    v = np.array(v)
    print(k, len(v), "mean %.3f min %.3f p5 %.3f max %.3f" % (v.mean(), v.min(), np.percentile(v, 5), v.max()))
