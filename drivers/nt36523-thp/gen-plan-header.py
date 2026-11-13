#!/usr/bin/env python3
"""Emit a pinned private C table, not firmware data or an executable transport."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT/'local/touch-plan'
PINS = {
 'stock-nt36523-thp.bin':'b7633e1ef2a271348d2142950e6235238bb4997b98b010bae0df6bc4a4af5b6b',
 'plan.json':'ba15eb49b220f678c889362f472054248f2934cb9a1d963e23957a1b70cf805a',
 'verification.json':'6a899603d6ee3eadba04b035fee3a49f4a25ed2cc40d0b745bdc4ccc0e8058b3',
 'read-allowlist.json':'b16ee0554a1e24aace302e9ca21182b4381ffc5e0f012ade8d8b2ac80a6ce201',
 'spi-transactions.json':'8322795dd250f8e96b27ed1a2bb6c50ac466fbd01447c8906e6aefbfc773d099',
 'write-whitelist.json':'371dda71ebcadc4c2aa08e8ef915dff55bb6cf90f33a13f9fdcf452e5b2dee86',
 'forbidden.json':'70b603d52747b677f7816f85efe7e32b0482a77aec6a182ef8e8c7e48656a12f',
}
FIELDS = ['id','address','length','source_offset','kind','wait_ms','read_slot','source_step',
          'consumer_step','max_reads','interval_ms','condition','minimum','maximum',
          'preserve_mask','set_mask','fixed','slice_sha256']

def sha(data): return hashlib.sha256(data).hexdigest()
def require(ok, why):
 if not ok: raise ValueError(why)

def load_inputs(inputs):
 raw={name:(inputs/name).read_bytes() for name in PINS}
 for name, expected in PINS.items():require(sha(raw[name])==expected,'input_sha256:'+name)
 spec=importlib.util.spec_from_file_location('plan230',Path(__file__).with_name('thp-plan.py'))
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 bundle=dict(fw=raw['stock-nt36523-thp.bin'],whitelist=json.loads(raw['write-whitelist.json']),
  transactions=json.loads(raw['spi-transactions.json']),forbidden=json.loads(raw['forbidden.json']),
  appendix=json.loads(raw['read-allowlist.json']),
  hashes={k:v[1] for k,v in m.INPUTS.items()}|{'read_allowlist':PINS['read-allowlist.json']})
 plan=json.loads(raw['plan.json']);verification=json.loads(raw['verification.json'])
 require(verification['passed'] and verification['plan_sha256']==PINS['plan.json'],'predecessor_verification')
 require(m.validate_symbolic_plan(plan,**bundle)['passed'],'symbolic_plan_validation')
 return raw,plan,bundle

def rows_for(plan, fw):
 steps=plan['steps']; ids={s['id']:i for i,s in enumerate(steps)}; rows=[]; read_indices=[]
 for i,s in enumerate(steps):
  row=dict.fromkeys(FIELDS,0);row.update(id=s['id'],read_slot=-1,source_step=-1,consumer_step=-1,
                                       fixed=[0]*4,slice_sha256=[0]*32)
  if s['direction']=='read':
   r=s['rule'];cond=r['success_condition'];kind={'transport_success':0,'equals':1,'range_inclusive':2}[cond['kind']]
   row.update(address=r['address'],length=r['length'],kind=3,read_slot=len(read_indices),
    max_reads=r['max_reads'],condition=kind,minimum=cond.get('value',cond.get('min',0)),maximum=cond.get('max',0),
    interval_ms=(r['repeat'].get('sleep_after_each_nonzero_ms',r['repeat'].get('interval_ms',0)) if r['repeat'] else 0),
    consumer_step=ids[r['read_value_consumers'][0]] if r['read_value_consumers'] else -1)
   read_indices.append(i)
  else:
   d=s['data'];row.update(address=s['address'],length=s['length'],wait_ms=s['wait_after_ms'])
   if d['kind']=='firmware_slice':
    off=d['source_offset'];payload=fw[off:off+s['length']]
    require(len(payload)==s['length'] and sha(payload)==d['sha256'],'slice_sha256:'+s['id'])
    row.update(kind=1,source_offset=off,slice_sha256=list(bytes.fromhex(d['sha256'])))
   elif d['kind']=='exact_hex':
    payload=bytes.fromhex(d['hex']);require(len(payload)==s['length']<=4,'fixed_width')
    row.update(kind=0,fixed=list(payload)+[0]*(4-len(payload)))
   elif d['kind']=='symbolic_rmw':
    source=ids[d['read_stage']];require(source==i-1 and steps[source]['rule']['address']==s['address'],'rmw_not_adjacent')
    row.update(kind=2,source_step=source,preserve_mask=d['preserve_mask'],set_mask=d['set_mask'])
   else:raise ValueError('unknown data kind')
  require(0<row['length']<=5117,'length')
  require(row['address']&127 not in [127,32,33],'shifted_address_denied')
  rows.append(row)
 require(len(rows)==46 and len(read_indices)==5,'table_shape')
 return rows,read_indices

def emit(rows,read_indices,fw):
 text=['/* SPDX-License-Identifier: GPL-2.0-only */','/* Generated offline; private output. No hardware authorization. */',
 '#ifndef NT36523_THP_PLAN_TABLE_H','#define NT36523_THP_PLAN_TABLE_H',
 '#define NT_FW_SIZE 196608U','#define NT_READ_LIMIT 214U','#define NT_MAX_PAYLOAD 5117U',
 '#define NT_STEP_COUNT 46U','#define NT_READ_SLOTS 5U',
 'enum nt_kind { NT_FIXED = 0, NT_SRAM = 1, NT_RMW = 2, NT_READ = 3 };',
 'enum nt_condition { NT_ANY = 0, NT_EQUAL = 1, NT_RANGE = 2 };',
 'struct nt_step {',
 ' const char *id; u32 address; u16 length; u32 source_offset;',
 ' u8 kind; u16 wait_ms; s16 read_slot; s16 source_step; s16 consumer_step;',
 ' u16 max_reads; u16 interval_ms; u8 condition; u8 minimum; u8 maximum;',
 ' u8 preserve_mask; u8 set_mask; u8 fixed[4]; u8 slice_sha256[32];','};',
 'static const u8 nt_firmware_sha256[32] = { '+', '.join(f'0x{x:02x}' for x in hashlib.sha256(fw).digest())+' };',
 'static const u16 nt_read_indices[NT_READ_SLOTS] = { '+', '.join(map(str,read_indices))+' };',
 'static const struct nt_step nt_plan[NT_STEP_COUNT] = {']
 for row in rows:
  values=[]
  for k in FIELDS:
   v=row[k]
   if k=='id':values.append(json.dumps(v))
   elif isinstance(v,list):values.append('{ '+', '.join(f'0x{x:02x}' for x in v)+' }')
   else:values.append(str(v))
  text.append(' { '+', '.join(values)+' },')
 text+=['};','#endif','']
 return '\n'.join(text).encode()

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
 try:
  out=args.output_dir.resolve();require(out.is_relative_to(RUN.resolve()),'output_scope');out.mkdir(parents=True,exist_ok=True)
  require(not (out/'thp-plan-table.h').exists(),'output_exists')
  raw,plan,bundle=load_inputs(args.inputs.resolve());rows,read_indices=rows_for(plan,bundle['fw']);header=emit(rows,read_indices,bundle['fw'])
  (out/'thp-plan-table.h').write_bytes(header)
  (out/'generation.json').write_text(json.dumps(dict(status='GENERATED_OFFLINE',execution_authorized=False,
   input_sha256={n:sha(b) for n,b in raw.items()},header_sha256=sha(header),fields=FIELDS,rows=rows,
   read_indices=read_indices,sram_chunks=sum(r['kind']==1 for r in rows),sram_bytes=sum(r['length'] for r in rows if r['kind']==1)),sort_keys=True,indent=2)+'\n')
  print(sha(header));return 0
 except (ValueError,KeyError,OSError) as e:print(str(e),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
