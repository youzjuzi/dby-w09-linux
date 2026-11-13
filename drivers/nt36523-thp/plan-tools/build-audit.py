# SPDX-License-Identifier: GPL-2.0-only
"""Generate an offline protocol dossier from manually reviewed disassembly and data.
Never loads, runs, emulates or interprets vendor instructions. Not a device tool.
Historical identifiers and narrative strings in generated JSON are pinned input
bytes checked by gen-plan-header.py; retain them to preserve reconstruction.
"""
from pathlib import Path
import sys
import json, hashlib, re, struct
R = Path(sys.argv[1]).resolve()
fw = (R/'inputs/stock-thp.bin').read_bytes()
afe = (R/'inputs/afe-p.so').read_bytes()
sha = lambda b: hashlib.sha256(b).hexdigest()
dump = lambda p, o: (R/p).write_text(json.dumps(o, ensure_ascii=False, indent=2)+'\n')
layout = json.loads((R/'inputs/firmware-layout.json').read_text())
sites = json.loads((R/'evidence/call-sites.json').read_text())
dis = (R/'evidence/afe.disassembly.txt').read_text().splitlines()
assert sha(fw) == layout['sha256'] and sha(afe) == layout['parent_sha256']
E = lambda *pcs: {'source':'offline', 'object':'AFE-P', 'virtual_addresses':list(pcs)}
rows = []
def add(id, direction, address, length, value, pc, **kw):
    row = dict(id=id, direction=direction, address=address,
               page_base=(hex(address & ~127) if isinstance(address,int) else None),
               register=(hex(address & 127) if isinstance(address,int) else None),
               length=length, data=value, evidence=E(pc),
               certainty='certain-static', wait_ms=0,
               success='transport return >= 0 only, unless specified',
               failure='low-level negative ioctl returns error; caller handling specified separately')
    row.update(kw); rows.append(row); return row
add('D01','write',0x3f0fe,1,{'hex':'69'},'0x29068',wait_ms=5,
    failure='nonzero return -> 0x29bc8 abort; NOT the three-attempt retry branch')
p0,p1=layout['parts'][:2]
for i,p in enumerate([p0,p1]):
    base=[0x3f128,0x3f118,0x3f100] if i==0 else [0x3f12c,0x3f130,0x3f104]
    pc=['0x290b8','0x290d0','0x290ec'] if i==0 else ['0x29110','0x29128','0x29144']
    for j,(key,n) in enumerate([('sram',3),('size_field',3),('crc',4)]):
        v=p[key] & (0x1ffff if key=='size_field' else (1<<(8*n))-1)
        add(f'D{2+i*3+j:02}','write',base[j],n,{'hex':v.to_bytes(n,'little').hex(),
            'source':f'firmware partition {i} {key}', 'firmware_header_offset':p['header_offset'],
            'field_file_offset':([p['header_offset']+4,p['header_offset']+8,0x18+4*i][j]),
            'field_width_bytes':4,'size_encoding':'size_field masked to 17 bits' if key=='size_field' else None},pc[j],
            failure='original ignores individual return here; do not reproduce this error policy')
add('D08','write',0x3f7e8,1,{'hex':'69'},'0x27da8',
    guard='cascade flag; this image sets cascade=1 at 0x284b8-0x284c0',
    caller='0x29154',failure='return not used by caller')
# Exact original host-side chunk arithmetic, computed on firmware data (not instruction execution).
chunks=[]
for p in layout['parts']:
    if not p['size_field']: continue
    for outer in range(0,p['bytes'],0xfc00):
        outerlen=min(0xfc00,p['bytes']-outer)
        for inner in range(0,outerlen,5117):
            n=min(5117,outerlen-inner); off=outer+inner; a=p['sram']+off
            assert (a&127) not in (127,32,33), 'unexpected shifted helper path'
            chunks.append({'ordinal':len(chunks)+1,'partition':p['index'],'address':a,
               'source_offset':p['source']+off,'length':n,
               'payload_sha256':sha(fw[p['source']+off:p['source']+off+n]),
               'outer_offset':outer,'inner_offset':inner,
               'page_select_hex':bytes([255,(a>>15)&255,(a>>7)&255]).hex(),
               'data_command_hex':f'{(a&127)|128:02x}',
               'wire_length':n+1,'shifted_prefix':False,
               'evidence':E('0x29e18-0x29fc4','0x29f54-0x29f80','0x144b0-0x14514','0x14ba4-0x14c30')})
add('D09','write','exact firmware ranges below',sum(p['bytes'] for p in layout['parts']),
    {'source':'stock-thp.bin, only nonzero size_field partitions, size_field+1 bytes',
     'chunks':chunks},'0x29f80',
    failure='nonzero SRAM return -> 0x29288 -> 0x296c0 -> retry entire download, maximum 3 attempts')
add('D10','read',0x3f7f1,1,None,'0x27ebc',wait_ms=1,
    repeat={'max_reads':200,'until':'RX[0]==0','sleep_after_each_nonzero_ms':1},
    failure='200 nonzero reads -> -1; caller 0x29334/0x293ec restarts full download, max 3 attempts; rwSPIBuf return itself ignored by helper')
add('D11','read',0x3f30e,1,None,'0x29418',failure='original ignores read return')
add('D12','write',0x3f30e,1,{'expression':'previous successful read OR 0x80','preserve_mask':127,'set_mask':128},'0x29440',failure='original ignores write return')
add('D13','write',0x2fe51,1,{'hex':'00'},'0x29454',failure='original ignores write return')
add('D14','write',0x2fe50,1,{'hex':'ae'},'0x29470',failure='original ignores write return')
add('D15','read',0x3f10d,1,None,'0x29484',failure='original ignores read return')
add('D16','write',0x3f10d,1,{'expression':'previous successful read OR 1','preserve_mask':254,'set_mask':1},'0x2953c',failure='original ignores write return')
add('D17','read',0x3f10d,1,None,'0x29550',success='value is logged; NOT compared for equality',failure='original ignores read return')
add('D18','read',0x2fe60,6,None,'0x281a4',caller='0x295f8',
    success='0xa0 <= RX[0] <= 0xaf',
    repeat={'initial_reads':1,'additional_reads':10,'interval_ms':10,'backedge_read_pc':'0x28204',
            'other_callers':'threshold != 0xa0 permits 50 additional reads'},
    failure='range timeout -> 0x296c0 -> full bootloader-reset/SRAM attempt, maximum 3; ioctl return ignored by helper')
for id,a,n,pc in [('F01',0x3f133,1,'0x297ac'),('F02',0x3f100,4,'0x29898'),
                  ('F03',0x3f120,4,'0x298ac'),('F04',0x3f104,4,'0x299ac'),('F05',0x3f124,4,'0x299c0')]:
    add(id,'read',a,n,None,pc,guard='after third failed full download only',
        success='diagnostic only',failure='no successful download inferred')
runtime=[]
def cmd(name,pc,c,h=187,p1=0,p2=0,certainty='certain-static',note=''):
    runtime.append(dict(id='CMD'+str(len(runtime)+1).zfill(2),function=name,direction='write',address=0x2fe50,
        page_base='0x2fe00',register='0x50',length=6,
        payload={'command':c,'handshake':h,'arg1':p1,'arg2':p2,'stamp':'s in 0..15','checksum':'-(command+handshake+arg1+arg2+s) & 255'},
        helper='sendLongCMDWithHandshake' if h != 187 else 'sendLongCMD',
        evidence=E(pc),certainty=certainty,wait_ms=0,
        success='helper return 0 is not proof of firmware/display completion',
        failure='abort in proposed C; original helper stamp polling/resend described in command_transport',
        display_effect='unknown; shared TDDI, excluded from C',plan_c_allowed=False,note=note))
for name,pc,c in [('stop','0x10c4c',0x11),('stopInspect','0x119e8',0x11),
 ('screen_off','0x126b0',0x13),('forceexitidle','0x12cc0',0x15),('enterproximity','0x13364',0x14),
 ('screen_on','0x137f8',0x15),('start_calibration','0x19768',0x23),('reset_idle_baseline','0x1bad4',0x2a),
 ('wakeup_gesture_scan_rate','0x1df04',0x3a),('suspend_state_A','0x227a8',0x7c),
 ('suspend_state_B','0x22860',0x7a),('resume','0x23174',0x7b)]:cmd(name,pc,c)
cmd('checkDDIC_parameter_error','0xbe1c',0x3a,note='conditional during forcestart/get_frame/UpdateDDIC; not read-only')
cmd('enable_freq_shift','0x18a70',0x1b,2)
cmd('disable_freq_shift','0x19284',0x1b,1)
cmd('forcefreq','0x1c85c',0x1b,1,'index+1',0)
cmd('baseline_update_interval','0x1a96c',0x3a,1,'interval low8','interval high8',note='raw on-wire arguments; physical units not established; caller also invokes UpdateDDIC')
cmd('idle_threshold','0x1b1c4',0x3a,2,'threshold low8','threshold high8')
cmd('idle_scan_rate','0x1b67c',0x3a,3,'derived rate code',0,note='code depends on cached rate; UpdateDDIC after command')
cmd('enter_idle','0x1c090',0x1d,2)
cmd('clear_calibration_status','0x1a110',0x3b,p1=1)
cmd('clear_frequency_status','0x1a248',0x3b,p1=2)
cmd('active_stylus_enable','0x249f0',0xc2,p1=1)
cmd('active_stylus_disable','0x24c00',0xc2,p1=0)
cmd('charger','0x25ba0','0x53 or 0x51',note='conditional on argument bit1; command byte numeric meaning inferred from function name')
for name,pc,h,a,b in [('stylus_detect','0x24fd8',0,'derived arg',0),('next_uplink_A','0x25390',2,'high8','low8'),
 ('next_uplink_B','0x25490',3,'high8','low8'),('stylus_id','0x26ba8',4,'id',0),('stylus_pressure','0x26dbc',5,'high8','low8')]:cmd(name,pc,0xca,h,a,b)
cmd('clear_stylus_status_A','0x25734',0xcb,p1=1)
cmd('clear_stylus_status_B','0x25998',0xcb,p1=2)
for pc,c in [('0x1e290',0x66),('0x1e3c0',0x66),('0x1e4d4',0x66),('0x1e5e8',0x66),
             ('0x1e668',0x6c),('0x1e678',0x61),('0x20ba0',0x6c),('0x20bb0',0x61),('0x20bc4',0x6e)]:
    cmd('inspect_MP_mode',pc,c,note='diagnostic/MP caller; command semantics not fully resolved, forbidden')
for pc in ['0x1e6f0','0x1e7fc','0x1e91c','0x1ea4c','0x1eb58','0x1fac0','0x2111c','0x21744']:
    cmd('inspect_MP_exit',pc,0,0,170,85,note='sendLongCMD selects handshake=0 for command0; diagnostic/MP only, forbidden')
cmd('mpTest_mode','0x1f790',0x47,170,3,0,note='sendLongCMD chooses aa for 0x41..0x4a; forbidden')
cmd('mpTest_command','0x1f7f8',0x21,note='MP only, forbidden')
for row in runtime:
    pc=row['evidence']['virtual_addresses'][0]
    target=next(c['target'] for c in sites if c['pc']==pc)
    row['helper']=target.split('@')[0]
aux=[]
for id,a,v,pc,fn in [('A01',0x3f04a,'5a','0xc014','checkDDIC repeated FE'),
 ('A02',0x3f04a,'5a','0x5ab0','PRINTDEBUG entry'),('A03',0x3f096,'03','0x8528','PRINTDEBUG diagnostic control'),
 ('A04',0x3f04a,'53','0xb4d0','PRINTDEBUG exit'),('A05',0x3f04a,'5a','0xc310','PRINTDEBUGmini entry'),
 ('A06',0x3f04a,'0d55','0xc340','PRINTDEBUGmini diagnostic unlock'),('A07',0x3f096,'03','0xe318','PRINTDEBUGmini control'),
 ('A08',0x3f04a,'53','0x10054','PRINTDEBUGmini exit')]:
    aux.append(dict(id=id,function=fn,direction='write',address=a,page_base=hex(a&~127),register=hex(a&127),
           length=len(v)//2,data={'hex':v},evidence=E(pc),certainty='certain-static',
           plan_c_allowed=False,meaning='watchdog/diagnostic control per log/call context; DDIC/persistence effects unknown'))
# Census includes direct B tail calls and raw ioctls, not only BL. Context is evidence, not guessed arguments.
census=[]
for c in sites:
    if c['target'].split('@')[0] not in ('rwSPIBuf','writeSPIData','sendLongCMD','sendLongCMDWithHandshake','ioctl'):continue
    if '+' in c['target']:continue  # intra-function B targets are not tail calls
    i=c['line']-1
    z=dict(c);z['context']=dis[max(0,i-24):i+3];z['arguments_resolved']=False
    z['plan_c_allowed']=False
    if c['caller'].startswith(('PRINTDEBUG','inspect','mpTest','mpItem','StackBackTrace','thp_afe_get_inspect')):
        z['category']='excluded diagnostic/MP/raw-memory path; address expressions in context; semantics not fully resolved'
    else:z['category']='reviewed normal/runtime/helper site; see transaction entries; unresolved calls default deny'
    census.append(z)
dump('evidence/spi-sink-census.json',census)
low={'spi_sync_ioctl':'0xc018b804','helper_write_flag':'bit31 of helper address; not on wire',
     'address_mask':'low 24 bits', 'max_payload_argument':196608,
     'split_payload_max':5117,'split_evidence':E('0x144b0-0x14514','0x14d1c-0x14d34'),
     'ordinary_page_select':{'direction':'write','bytes':'ff (address>>15)&255 (address>>7)&255',
          'length':3,'then_cs_deassert':True,'evidence':E('0x1470c-0x14754','0x14ba4-0x14bec')},
     'ordinary_read':{'tx':'(address&127), dummy clocks; length payload+2','rx':'payload from RX+2',
          'evidence':E('0x14760-0x147b8','0x14a14-0x14a58')},
     'ordinary_write':{'tx':'(address&127)|128 followed by payload','length':'payload+1',
          'evidence':E('0x14b1c-0x14c30')},
     'exceptions':[{'guard':'initial masked page == 0x7fff80','effect':'page select omitted','evidence':E('0x144d4','0x145d8','0x14704','0x14b9c')},
       {'guard':'low7 in [127,32,33] and page != sentinel',
        'effect':'read from low7-2 for two extra bytes; writes first read 4 wire bytes, preserve RX[2:4], prepend those two bytes then payload at low7-2',
        'evidence':E('0x145ec-0x14610','0x146e8-0x146f8','0x148c4-0x14a10'),
        'download_effect':'none for all 29 expanded SRAM chunks and normal D01-D18; F03 read hits low7=32'},
       {'guard':'effective address == 0x7fffa0', 'effect':'raw 0x20 length payload+2; no page; copy RX+2',
        'evidence':E('0x14380-0x143e4')}],
     'failure':'negative ioctl -> return; no reset/retry in rwSPIBuf; callers may retry or ignore failures'}
for row in rows:
    a=row['address']
    if not isinstance(a,int):continue
    shift=(a&127) in (127,32,33)
    n=row['length']
    row['physical_spi']=[{'direction':'write','role':'page-select','length':3,
        'tx_hex':bytes([255,(a>>15)&255,(a>>7)&255]).hex(),
        'evidence':E('0x14754' if row['direction']=='read' else '0x14bec')},
        {'direction':row['direction'],'role':'payload','command':(((a&127)-2)&255 if shift else a&127) | (128 if row['direction']=='write' else 0),
         'length':n+(4 if shift else 2) if row['direction']=='read' else n+1,
         'data':row['data'], 'rx_payload_offset':(4 if shift else 2) if row['direction']=='read' else None,
         'evidence':E('0x14790' if row['direction']=='read' else '0x14c30')}]
command_transport={
 'sendLongCMD':{'write_pcs':['0x11310','0x11754'], 'stamp_read_pcs':['0x11544','0x11644','0x11674','0x116a4','0x116d4','0x1170c'],
 'stamp_address':0x2fe77,'stamp_length':1,'max_reads':6,'wait_ms':0,'max_writes':2,
 'logic':'cmd=0 -> handshake 0; cmd 0x41..0x4a -> aa; otherwise bb. stamp modulo16. internal mode can skip reads. Matching stamp returns 0; ff or exhausted reads causes one resend, not robust completion verification.'},
 'sendLongCMDWithHandshake':{'write_pcs':['0x18df4'],
 'stamp_read_pcs':['0x1900c','0x19108','0x19138','0x19168','0x19198','0x191c8'],
 'stamp_address':0x2fe77,'stamp_length':1,'max_reads':6,'wait_ms':0,'max_writes':1,
 'logic':'explicit handshake argument; internal mode can skip polls; exhaustion can still return success. Physical units/firmware command semantics not proved by helper success.'}}
post=[{'id':'P01','direction':'read','wire_command_hex':'21','wire_length':6,'payload_offset':2,
       'evidence':E('0x4c28-0x4d60'), 'wait_before_ms':10, 'max_iterations':41,
       'success':'checkDDIC returns 0 and cached status bit22 set',
       'failure':'final iteration calls PRINTDEBUG before checkDDIC; timeout calls PRINTDEBUGmini/HIST, which WRITE; forbidden in C'},
      {'id':'P02','direction':'read','address':0x3f0f0,'length':4,'evidence':E('0xb630'),
       'guard':'checkDDIC input first byte bit6 clear',
       'success':'sum((byte0&0xbf),byte1,byte2,byte3)&255 == 0',
       'failure':'status/parameter errors -> CMD13/A01 or error; not passive'},
      {'id':'P03','direction':'read','wire_command_hex':'21','wire_length':6,
       'evidence':E('0x1ac60-0x1b068','0x1ad1c','0x1adec'),
       'function':'UpdateDDIC','max_iterations':21,'wait_ms':1,
       'success':'checkDDIC/cached status gate','failure':'PRINTDEBUGmini/HIST; no direct flash programming in UpdateDDIC body'},
      {'id':'P04','direction':'read','wire_command_hex':'20','wire_length':3257,
       'evidence':{'source':'offline','file':'inputs/hostprocessing/novatek_thp.c','lines':[564,594]},
       'caller_evidence':E('0x15af8-0x15b14','0x15d58'),
       'interface':'GET_FRAME 0xc018b801, raw RX copied to userspace; IRQ queue; no mmap',
       'failure':'stock kernel get_frame logs transfer failure but returns 0; C must propagate error'},
      {'id':'P05','direction':'read','address':0x208b4,'length':96,
       'evidence':E('0x13e20-0x13e38'),
       'function':'get_info','certainty':'certain-static; excluded from C'} ,
      {'id':'P06','direction':'read','address':0x20914,'length':136,'function':'get_hw_cap',
       'evidence':E('0x15548-0x15564'),'certainty':'certain-static; excluded from C'}]
protocol={'schema_version':1,'experiment':'EXP-0219','date':'2026-10-19','source_sha256':sha(afe),
 'firmware_sha256':sha(fw),'execution_authorized':False,
 'coverage':{'normal_download':'D01-D18, all nonempty partition chunks expanded; reads/retries represented as bounded loops',
             'whole_afe_complete':False,'reason':'all direct sinks enumerated; diagnostic/MP/dynamic register operands and MCU internals not fully semantically decoded; do not claim exhaustive no-persistent-write proof',
             'direct_sink_count':len(census),'sink_census':'evidence/spi-sink-census.json'},
 'host_only_prefix':[{'function':'forceopen','pcs':['0x39d0-0x3c68'], 'action':'select embedded object, checksum 0x30000 bytes; no SPI'},
                     {'function':'open_project','pcs':['0x3e8c-0x4664'],'action':'compare project, store host state and object pointer; no SPI'}],
 'forcestart_host_ioctls':[{'pc':pc,'request':req,'argument':a,'effect':e} for pc,req,a,e in [
    ('0x4bc4','0x4004b807',0,'disable IRQ'),('0x5188','0x4004b803','guiNORMALDTTO','set timeout'),
    ('0x5308','0x4004b809',0,'clear frame queue'),('0x5504','0x4004b806',1,'set blocking'),
    ('0x55c8','0x4004b807',1,'enable IRQ'),('0x5728','0x400cb80d','status struct','AFE status notification')]],
 'transport':low, 'download':rows,'partition_layout':layout['parts'],
 'retry':{'parse_max_attempts':3,'parse_backedge':'0x28e6c-0x28e7c','hardware_max_attempts':3,
     'hardware_backedge':'0x296c0-0x296d0 -> 0x2905c','before_retry':'full bootloader reset69 and 5ms then CRC-bank setup/SRAM copy',
     'important':'forcestart at 0x4bc8 ignores nvt_update_firmware return; do not reproduce',
     'proposed_C_hardware_max_attempts':1},
 'command_transport':command_transport,'runtime_commands':runtime,'diagnostic_writes':aux,'read_paths':post,
 'kernel_runtime':{
   'source':'inputs/hostprocessing/novatek_thp.c',
   'type_selection':{'vendor_ic_type':5,'command_address':0x2fe50,'stamp_address':0x2fe77,'lines':[105,120]},
   'reset_idle':{'direction':'write','address':0x3f0fe,'data_hex':'aa','wait_ms':10,'lines':[263,297],
                 'physical_spi':['ff 07 e1','fe aa'],'C_allowed':False},
   'bootloader_reset':{'direction':'write','address':0x3f0fe,'data_hex':'69','wait_ms':10,'lines':[299,333],
                 'physical_spi':['ff 07 e1','fe 69'],'note':'kernel identification wait differs from AFE 5ms','C_allowed':False},
   'fw_command':{'read_before':{'address':0x2fe77,'length':1,'rx_offset':2,'lines':[875,914]},
     'write':{'address':0x2fe50,'length':6,
       'payload':['command','bb','status-arg1','status-arg2','previous_stamp-1 modulo256','negative sum8'],
       'lines':[975,1030]},
     'poll':{'address':0x2fe77,'length':1,'max_reads':5,'interval_ms':'send_cmd_retry_delay_ms or 1',
             'success':'stamp matches sent byte, then sleep50ms','lines':[1030,1060]},
     'pre_delay_ms':'120 if aod_support_on_tddi',
     'commands':[{'value':0x1a,'name':'sleep','trigger':'complementary AOD low-power branch or sleep helper'},
                 {'value':0x13,'name':'gesture','trigger':'gesture branch'},
                 {'value':0x7d,'name':'customized DP DSTB','trigger':'PT/poweroff/second_poweroff branches'},
                 {'value':0xc2,'name':'stylus status','trigger':'BT status notification; max3 attempts'}],
     'C_allowed':False},
   'resume':{'sequence':['CS high','AA software idle plus10ms','reset high','configured resume delay1ms'],
             'lines':[627,650],'C_allowed':False},
   'suspend':{'sequence':'conditional fw command 7d/13/1a; reset/CS low depending PT/gesture/AOD/suspend_no_reset; configured1ms or fixed30ms delay',
              'lines':[1154,1256],'C_allowed':False}},
 'unresolved_direct_sink_calls':census}
dump('spi-transactions.json',protocol)
# Conditional construction whitelist. Explicitly does NOT grant device execution.
allow=[]
for p in layout['parts']:
    if not p['size_field']:continue
    a=p['sram'];n=p['bytes'];off=p['source']
    allow.append({'id':'SRAM'+str(p['index']),'kind':'volatile_sram','start':a,'end_exclusive':a+n,
      'direction':'write','data_rule':{'kind':'exact_firmware_slice','sha256':sha(fw),
           'source_offset':off,'length':n,'slice_sha256':sha(fw[off:off+n])},
      'max_total_bytes':n,'max_replays':0,'evidence':E('0x28840-0x28db0','0x29e18-0x29fc4'),
      'certainty':'certain destination and bytes; volatile SRAM classification supported by source map, MCU behavior unknown'})
for row in rows:
    if row['direction']!='write' or not isinstance(row['address'],int):continue
    a=row['address'];allow.append({'id':row['id'],'kind':'volatile_runtime_register',
       'start':a,'end_exclusive':a+row['length'],'direction':'write','max_occurrences':1,
       'data_rule':row['data'],'must_follow':'exact D01-D18 ordered path, D09 between D08/D10',
       'requires_valid_prior_read':row['id'] in ('D12','D16'),
       'evidence':row['evidence'],'certainty':'address/data certain; register function from download implementation, not electrical safety proof'})
wl={'schema_version':1,'experiment':'EXP-0219','policy':'conditional design whitelist, default deny',
 'execution_authorized':False,'can_enter_plan_c':False,
 'firmware_sha256':sha(fw),'afe_sha256':sha(afe),
 'global_preconditions':['separate explicit user authorization for development/build, SD preparation, reset, SRAM download, frame capture, reboot',
  'four EXP-0216 gates closed including exact trim/project/firmware identity and display rail/reset timing',
  'LCD init complete, both halves stable; no DPMS/suspend/watchdog reset/panel reinit during run',
  'one download only; every return checked; no PRINTDEBUG/checkDDIC recovery/UpdateDDIC/MP/calibration'],
 'allowed':allow,
 'transport_only':[{'kind':'page_select','bytes_rule':'ff high8 low8 derived solely from the next allowed read/write address',
                    'evidence':E('0x1470c-0x14754','0x14ba4-0x14bec')},
                   {'kind':'read_command_clocks','values_rule':'only exact read command + dummy clocks in the reviewed read schedule',
                    'note':'read SPI still transmits MOSI; not permission for arbitrary command bytes'}],
 'restrictions':{'allowed_byte_range_is_not_enough':True,'exact_values_order_and_counts_required':True,
   'no_generic_read_modify_write':True,'rmw_only':[{'address':0x3f30e,'width':1,'set_mask':128},
                                               {'address':0x3f10d,'width':1,'set_mask':1}],
   'rmw_prior_read_failure':'abort; never OR an uninitialized/stale byte',
   'all_other_writes':'deny','zero_size_partitions':'deny','flash_otp_calibration_ddic_persistence':'deny'}}
dump('write-whitelist.json',wl)
deny=[
 ('persistent','Any flash erase/program/write-enable/unprotect, OTP/eFuse/calibration/OEM/DDIC NVM write',None,
  'No such explicit sequence identified in normal download. Opcode bytes alone (06/02/20/d8/c7/60 or GCM token) are not proof without address/protocol. Any undocumented command denied.'),
 ('arbitrary_spi','SPI_SYNC or rwSPIBuf arbitrary addresses/values; sentinel page skip and shifted-prefix writes',E('0x145ec-0x14610','0x148c4-0x14a10'),'No bypass through generic transport; normal approved download chunks do not require shifted writes.'),
 ('wrong_status_clear','Write 0x2fe60=0 or substitute for 0x2fe51',E('0x29454','0x281a4'),'Different registers; stock only reads 0x2fe60 here. Do not merge EXP-0170 control profile.'),
 ('retries','Second download/reset or automatic recovery after any failure',E('0x296c0-0x296d0'),'Original repeats up to 3; C must stop after first attempt.'),
 ('ddic','checkDDIC/UpdateDDIC recovered paths',E('0xbe1c','0xc014','0x1adec'),'Conditional writes and diagnostic calls, not read-only.'),
 ('debug','PRINTDEBUG/PRINTDEBUGmini/HIST/inspect/MP/raw getters/StackBackTrace',E('0x5ab0','0xc310','0xc340','0x8528','0xe318','0xb4d0','0x10054'),'Debug functions write watchdog/diagnostic registers and can be called by download timeout/GET_FRAME errors.'),
 ('runtime','All 6-byte commands at 0x2fe50 including rate/baseline/calibration/sleep/wake/stylus',E('0x11310','0x18df4'),'Command bytes decoded in spi-transactions.json, firmware/display/persistence meaning not fully established. Only 1-byte ae during download is conditional allow.'),
 ('gpio_pm','GPIO98 low, CS forced low, suspend/resume, IRQ reenable after stop, panel power cycling',
  {'source':'offline','file':'inputs/hostprocessing/novatek_thp.c','lines':[627,650,1154,1256]},'No safe final reset/sleep state demonstrated; host stop must not call old driver cleanup.'),
 ('kernel_passthrough','Arbitrary ioctl including RESET/SET_SPI_SPEED/FINISH_NOTIFY/SET_AFE_STATUS',
  {'source':'offline','file':'inputs/hostprocessing/huawei_thp.h','lines':[169,195]},'Must review each intended action; existing /dev/thp API does not enforce register whitelist.'),
 ('private_inputs','Executing/loading/emulating/distributing private AFE, algorithm or parameter libraries',None,'This audit authorizes static reads only.')]
dump('forbidden.json',{'schema_version':1,'experiment':'EXP-0219','default':'deny',
 'execution_authorized':False,'entries':[dict(id=i,operation=o,evidence=e,reason=s) for i,o,e,s in deny]})
dump('evidence/frame-layout.json',{
 'source':'offline','certainty':'static parser behavior; no device frames',
 'get_frame_size':3257,'raw_transport_command':'20','raw_rx_unmodified_by_kernel':True,
 'parser_base':{'expression':'rawRX + 6 + guiFRONTBUFFER[project]', 'frontbuffer':0,
               'evidence':E('0x15c34-0x15c40','0x164fc-0x16518','0x5a680')},
 'main_checksum':{'input_relative_start':0,'length':3214,'stored_relative_offset':3214,
   'width':16,'endian':'little','algorithm':'(-sum(LE16 pairs of first 3214 bytes)) & 65535',
   'all_zero_exception':'checksum returns 0x5555aaaa for effectively all-zero data; low16=aaaa',
   'evidence':E('0x174e8-0x17518','0x3cc4-0x3ccc','0x3d80-0x3e70','0x27cdc-0x27d04')},
 'frame_counter':{'relative_offset':3212,'raw_offset':3218,'width':16,'endian':'little',
     'evidence':E('0x17520-0x17538','0x17540-0x17560')},
 'ordinary_samples':{'relative_offset':0,'raw_offset':6,'bytes':3200,'sample_bits':16,'endian':'little',
    'sign':'unresolved physical meaning; assembly stores bit patterns without sign extension',
    'evidence':E('0x17930-0x179e4','0x31d8-0x3260')},
 'segmented_samples':{'marker_raw_offset':6,'marker_hex':'a55a5aa5','sequence_relative_offset':4,
    'sequence_values':[1,2,3,4],'payload_relative_offset':6,'payload_raw_offset':12,
    'first_three_segments_bytes_each':800,
    'fourth_segment_bytes':800,'fourth_segment_note':'size expression from main data length - 0x95a then source skips 6; with 3200 -> 800 bytes',
    'evidence':E('0x15f10-0x15f94','0x1787c-0x1792c','0x179f0-0x17b04')},
 'extra_area':{'buttons_bytes':12,'label_confidence':'symbol guiBTN_DATALEN; not proof of self capacitance',
    'additional_checksum':{'input_relative_start':3216,'length':30,'stored_relative_offset':3246},
    'pen_aux_fields':'relative 3216 onward; parser extracts several LE16 fields and flags; semantics/layout not fully established',
    'evidence':E('0x17b0c-0x17bec','0x17cbc-0x17cf0','0x17f7c-0x17ff0','0x5a668')},
 'geometry':{'tx_static':16,'rx_static':50,'data_words':1600,
    'interpretation':'32x50 fits 1600 words, but could be two 16x50 planes/cascade/time multiplex. No definitive row/channel ordering.'},
 'unknown':['first raw 6 bytes status bit semantics','signedness/absolute-versus-delta',
            '16x50/32x50 stitching','self capacitance versus button area','pen electrode matrix',
            'physical scan/frame rates','exact row/column orientation'],
 'needed_evidence':['unmodified valid raw RX with timestamp/IRQ count at empty screen, isolated finger positions, stylus only and finger+stylus',
                    'startup trace including status/markers/firmware identity with an independently side-effect-audited capture method']})
print(json.dumps({'download_records':len(rows),'sram_chunks':len(chunks),'runtime_commands':len(runtime),
 'diagnostic_writes':len(aux),'direct_sink_sites':len(census),'whitelist_entries':len(allow),'can_enter_C':False}))
