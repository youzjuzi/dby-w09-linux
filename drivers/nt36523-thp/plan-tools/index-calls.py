# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import sys
import re,json,struct
O=Path(sys.argv[1]).resolve();lines=(O/'evidence/afe.disassembly.txt').read_text().splitlines();func='';functions={};calls=[]
for i,line in enumerate(lines):
 m=re.match(r'([0-9a-f]+) <(.+)>:',line)
 if m:func=m[2].split('@@')[0];functions[func]={'address':int(m[1],16),'lines':[]}
 if func:functions[func]['lines'].append(line)
 m=re.search(r'^\s*([0-9a-f]+):\s+[0-9a-f]+\s+(bl|b)\s+([0-9a-f]+) <([^>]+)>',line)
 if m:calls.append(dict(caller=func,pc='0x'+m[1],kind=m[2],target_pc='0x'+m[3],target=m[4],line=i+1))
(O/'evidence/call-sites.json').write_text(json.dumps(calls,indent=2))
