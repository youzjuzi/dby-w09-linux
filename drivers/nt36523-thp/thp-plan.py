#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Project offline plan validator; complete historical derivation remains unverified.
"""Offline symbolic download plan; no hardware executor."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'local/touch-plan'
INPUTS = {
    'firmware': ('stock-nt36523-thp.bin', 'b7633e1ef2a271348d2142950e6235238bb4997b98b010bae0df6bc4a4af5b6b'),
    'whitelist': ('write-whitelist.json', '371dda71ebcadc4c2aa08e8ef915dff55bb6cf90f33a13f9fdcf452e5b2dee86'),
    'transactions': ('spi-transactions.json', '8322795dd250f8e96b27ed1a2bb6c50ac466fbd01447c8906e6aefbfc773d099'),
    'forbidden': ('forbidden.json', '70b603d52747b677f7816f85efe7e32b0482a77aec6a182ef8e8c7e48656a12f'),
    'format': ('nt36523-format.h', '419a3f8aff379da8644908a1074a757f6059710b2f54266aab1402a03732f80a'),
}
ORDER = [f'D{i:02}' for i in range(1, 19)]
MUST_FOLLOW = 'exact D01-D18 ordered path, D09 between D08/D10'
FORBIDDEN_IDS = {'persistent', 'arbitrary_spi', 'wrong_status_clear', 'retries',
                 'ddic', 'debug', 'runtime', 'gpio_pm', 'kernel_passthrough', 'private_inputs'}
EXPECTED_COUNTS = {'nonempty_partitions': 4, 'sram_write_count': 29, 'sram_bytes': 131984}
OP_KEYS = {'id', 'stage', 'policy_id', 'direction', 'operation', 'address', 'length',
           'data', 'occurrence', 'wait_after_ms'}


class Rejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def need(condition, code, message):
    if not condition:
        raise Rejected(code, message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def regular_bytes(path):
    path = Path(path)
    need(stat.S_ISREG(path.lstat().st_mode), 'not_regular_file', str(path))
    return path.read_bytes()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate_json_key', key)
        result[key] = value
    return result


def load_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


def le32(data, offset):
    need(type(offset) is int and 0 <= offset <= len(data) - 4,
         'field_bounds', str(offset))
    return struct.unpack_from('<I', data, offset)[0]


def parse_container(fw):
    """Port only nt_parse container rules, never nt_control_allowed."""
    need(len(fw) == 196608, 'firmware_size', str(len(fw)))
    tail = next((t for t in range(len(fw), 0, -4096)
                 if fw[t-3:t] in (b'NVT', b'MOD')), 0)
    need(tail and fw[tail-4096] + fw[tail-4095] == 255,
         'container_tail', 'missing tail/version complement')
    end = le32(fw, 0)
    cascade = bool(fw[0x20] & 2)
    stop = end // 2 if cascade else end
    need(end <= tail and 0x30 <= stop <= 0x400 and (stop-0x30) % 16 == 0
         and (not cascade or end % 2 == 0), 'container_header', 'invalid header extent')
    info = (stop-0x30)//16 + int(cascade)
    overlay = fw[0x28] & 15 if fw[0x28] & 0x10 else 0
    count = 2 + info + overlay
    need(count <= 64, 'partition_count', str(count))
    result = []
    for i in range(count):
        if i < 2:
            pos = i*12
            source, address, size = (le32(fw, pos+j) for j in (0, 4, 8))
            crc = le32(fw, 0x18+i*4)
        else:
            if i < 2+info:
                pos = end-16 if cascade and i == 1+info else 0x30+(i-2)*16
            else:
                pos = result[1]['source']+(i-2-info)*16
            need(0 <= pos <= tail-16, 'partition_header_bounds', str(i))
            address, size, source, crc = (le32(fw, pos+j) for j in (0, 4, 8, 12))
        length = size+1 if size else 0
        need(not (i < 2 and size == 0), 'zero_partition', str(i))
        if length:
            need(source < tail and length <= tail-source and address < 0x2fe00
                 and length <= 0x2fe00-address, 'partition_bounds', str(i))
        result.append(dict(index=i, header_offset=pos, source=source, sram=address,
                           size_field=size, bytes=length, crc=crc))
    return result


def slice_bytes(fw, item):
    rule = item['data_rule']
    need(rule.get('kind') == 'exact_firmware_slice', 'unknown_slice_rule', item['id'])
    off, length = rule['source_offset'], rule['length']
    need(type(length) is int and length > 0, 'zero_partition', item['id'])
    need(type(off) is int and 0 <= off <= len(fw)-length, 'slice_bounds', item['id'])
    data = fw[off:off+length]
    need(digest(data) == rule['slice_sha256'], 'slice_sha256', item['id'])
    return data


def register_data(fw, item):
    rule = item['data_rule']
    length = item['end_exclusive'] - item['start']
    if item['requires_valid_prior_read']:
        need(set(rule) == {'expression', 'preserve_mask', 'set_mask'},
             'rmw_rule', item['id'])
        return {'kind': 'unresolved_prior_read', 'preserve_mask': rule['preserve_mask'],
                'set_mask': rule['set_mask'], 'read_stage': {'D12': 'D11', 'D16': 'D15'}[item['id']]}
    if 'field_file_offset' in rule:
        need(rule['field_width_bytes'] == 4, 'field_width', item['id'])
        value = le32(fw, rule['field_file_offset'])
        if rule['size_encoding'] == 'size_field masked to 17 bits':
            value &= 0x1ffff
        else:
            need(rule['size_encoding'] is None, 'field_encoding', item['id'])
        need(value < 1 << (length*8), 'field_truncation', item['id'])
        data = value.to_bytes(length, 'little')
        need(data.hex() == rule['hex'], 'field_value_mismatch', item['id'])
    else:
        need(set(rule) == {'hex'}, 'unknown_data_rule', item['id'])
        data = bytes.fromhex(rule['hex'])
    need(len(data) == length, 'register_width', item['id'])
    return {'kind': 'exact_hex', 'hex': data.hex()}


def context(fw, whitelist, transactions, forbidden):
    need(whitelist['schema_version'] == transactions['schema_version'] == forbidden['schema_version'] == 1,
         'schema_version', 'only reviewed schema v1')
    need(not any(x['execution_authorized'] for x in (whitelist, transactions, forbidden)),
         'authorization_change', 'offline only')
    allowed = {x['id']: x for x in whitelist['allowed']}
    writes = [x for x in transactions['download'] if x['id'] in ORDER and x['direction'] == 'write']
    register_ids = {x['id'] for x in writes if x['id'] != 'D09'}
    need(set(allowed) == register_ids | {'SRAM0', 'SRAM1', 'SRAM14', 'SRAM15'}
         and len(allowed) == len(whitelist['allowed']), 'policy_items', 'unknown/missing allowed item')
    need({x['id'] for x in forbidden['entries']} == FORBIDDEN_IDS and forbidden['default'] == 'deny',
         'forbidden_schema', 'unhandled semantic rule')
    need(whitelist['restrictions']['zero_size_partitions'] == 'deny'
         and whitelist['restrictions']['all_other_writes'] == 'deny', 'policy_default', 'must deny')
    path = [x for x in transactions['download'] if x['id'] in ORDER]
    need([x['id'] for x in path] == ORDER, 'transaction_order', 'not D01..D18')
    parts = parse_container(fw)
    need(parts == transactions['partition_layout'] or all(
        all(p[k] == q[k] for k in p) for p, q in zip(parts, transactions['partition_layout']))
        and len(parts) == len(transactions['partition_layout']), 'partition_table_mismatch', 'container vs audit')
    for item in allowed.values():
        need(item['direction'] == 'write' and item['start'] < item['end_exclusive'],
             'policy_range', item['id'])
        if item['kind'] == 'volatile_sram':
            i = int(item['id'][4:]); p = parts[i]
            slice_bytes(fw, item)
            need(p['bytes'] > 0 and p['source'] == item['data_rule']['source_offset']
                 and p['sram'] == item['start'] and p['bytes'] == item['data_rule']['length']
                 == item['end_exclusive']-item['start'] == item['max_total_bytes']
                 and item['max_replays'] == 0, 'sram_policy_mismatch', item['id'])
        else:
            need(item['kind'] == 'volatile_runtime_register' and item['must_follow'] == MUST_FOLLOW
                 and item['max_occurrences'] == 1, 'register_policy', item['id'])
            tx = next(x for x in path if x['id'] == item['id'])
            need((tx['address'], tx['length'], tx['data']) ==
                 (item['start'], item['end_exclusive']-item['start'], item['data_rule']),
                 'transaction_policy_mismatch', item['id'])
            register_data(fw, item)
    need(digest(fw) == whitelist['firmware_sha256'] == transactions['firmware_sha256'],
         'firmware_sha256', 'full identity mismatch')
    need(sorted(whitelist['restrictions']['rmw_only'], key=lambda x: x['address']) ==
         sorted([{'address': allowed[k]['start'], 'width': 1,
                  'set_mask': allowed[k]['data_rule']['set_mask']} for k in ['D12', 'D16']],
                key=lambda x: x['address']), 'rmw_policy', 'must match both reviewed items')
    return allowed, path, parts


def derive_chunks(fw, allowed, transactions):
    """Outer 0xfc00 split, then inner 5117; no artificial 128-byte split."""
    need(transactions['transport']['split_payload_max'] == 5117, 'chunk_limit', 'unknown transport')
    result = []
    for item in sorted((x for x in allowed.values() if x['kind'] == 'volatile_sram'),
                       key=lambda x: int(x['id'][4:])):
        data = slice_bytes(fw, item)
        for outer in range(0, len(data), 0xfc00):
            outer_length = min(0xfc00, len(data)-outer)
            for inner in range(0, outer_length, 5117):
                off = outer+inner; length = min(5117, outer_length-inner)
                address = item['start']+off
                need(address & 127 not in (127, 32, 33), 'shifted_prefix', item['id'])
                result.append(dict(ordinal=len(result)+1, partition=int(item['id'][4:]),
                                   address=address, source_offset=item['data_rule']['source_offset']+off,
                                   length=length, payload_sha256=digest(data[off:off+length]),
                                   outer_offset=outer, inner_offset=inner))
    reviewed = next(x for x in transactions['download'] if x['id'] == 'D09')['data']['chunks']
    need(len(result) == len(reviewed) and all(all(x[k] == y[k] for k in x)
         and y['shifted_prefix'] is False for x, y in zip(result, reviewed)),
         'chunk_table_mismatch', 'derived chunks vs transaction table')
    return result


def make_plan(fw, whitelist, transactions, forbidden, input_hashes):
    allowed, path, parts = context(fw, whitelist, transactions, forbidden)
    chunks = derive_chunks(fw, allowed, transactions)
    operations, dependencies, schedule = [], [], []
    occurrences = collections.Counter()
    for tx in path:
        stage = tx['id']
        if tx['direction'] == 'read':
            dependencies.append(dict(id=stage, address=tx['address'], length=tx['length'],
                                     direction='read', approved=False, repeat=tx.get('repeat'),
                                     success=tx['success'], wait_ms=tx['wait_ms']))
            schedule.append({'kind': 'unapproved_read_dependency', 'id': stage})
            continue
        selected = chunks if stage == 'D09' else [None]
        for chunk in selected:
            key = 'SRAM'+str(chunk['partition']) if chunk else stage
            item = allowed[key]; occurrences[key] += 1
            if chunk:
                data = {'kind': 'firmware_slice', 'source_offset': chunk['source_offset'],
                        'sha256': chunk['payload_sha256']}
                ident, address, length = f"D09.{chunk['ordinal']:03}", chunk['address'], chunk['length']
            else:
                data = register_data(fw, item)
                ident, address, length = stage, item['start'], item['end_exclusive']-item['start']
            op = dict(id=ident, stage=stage, policy_id=key, direction='write',
                      operation='rmw' if item.get('requires_valid_prior_read') else 'write',
                      address=address, length=length, data=data, occurrence=occurrences[key],
                      wait_after_ms=tx['wait_ms'])
            operations.append(op); schedule.append({'kind': 'write', 'id': ident})
    plan = dict(schema_version=1, experiment='EXP-0229', execution_authorized=False,
                status='REJECTED_INCOMPLETE', input_sha256=input_hashes,
                scope='logical memory writes; page-select/dummy clocks and runtime polls not emitted',
                operations=operations, required_reads=dependencies, schedule=schedule,
                skipped_zero_partitions=[p['index'] for p in parts if not p['bytes']])
    plan['totals'] = totals(operations, allowed)
    return plan


def totals(operations, allowed):
    sram = [x for x in operations if allowed.get(x.get('policy_id'), {}).get('kind') == 'volatile_sram']
    unresolved = [x for x in operations if x['data']['kind'] == 'unresolved_prior_read']
    return dict(nonempty_partitions=len({x['policy_id'] for x in sram}),
                sram_write_count=len(sram), sram_bytes=sum(x['length'] for x in sram),
                write_count=len(operations), write_payload_bytes=sum(x['length'] for x in operations),
                exact_write_count=len(operations)-len(unresolved),
                exact_payload_bytes=sum(x['length'] for x in operations if x not in unresolved),
                unresolved_write_count=len(unresolved))


def forbidden_hit(op, forbidden, transactions):
    """Prose categories stay default-deny; never infer semantics from opcode alone."""
    categories = {x['id'] for x in forbidden['entries']}
    if op.get('operation') in categories:
        return op['operation']
    address, length = op.get('address'), op.get('length')
    if type(address) is not int or type(length) is not int:
        return None  # structural validation rejects this below
    end = address+length
    if length > 0 and address <= 0x2fe60 < end:
        return 'wrong_status_clear'
    if address == 0x2fe50 and length == 6:
        return 'runtime'
    if any(address < d['address']+d['length'] and end > d['address']
           for d in transactions['diagnostic_writes']):
        return 'debug'
    if address == 0x3f0fe and op.get('data', {}).get('hex') == 'aa':
        return 'arbitrary_spi'
    return None


def validate_plan(plan, fw, whitelist, transactions, forbidden):
    allowed, path, parts = context(fw, whitelist, transactions, forbidden)
    issues, checks = [], []
    def issue(code, ident, detail, severity='error'):
        issues.append(dict(code=code, id=ident, detail=detail, severity=severity))
    occurrences, amounts, intervals = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    ranks = []
    seen_ids = set()
    for op in plan.get('operations', []):
        ident = op.get('id', '<missing>'); before = len(issues)
        hit = forbidden_hit(op, forbidden, transactions)
        if hit:
            issue('forbidden:'+hit, ident, 'semantic deny / reviewed forbidden address')
        if set(op) != OP_KEYS:
            issue('operation_schema', ident, 'missing or extra operation fields')
        if op.get('operation') == 'rmw':
            matched = any(op.get('address') == x['address'] and op.get('length') == x['width']
                          for x in whitelist['restrictions']['rmw_only'])
            if not matched:
                issue('rmw_outside_only', ident, 'no matching rmw_only entry')
        item = allowed.get(op.get('policy_id'))
        addr, length = op.get('address'), op.get('length')
        if not item or type(addr) is not int or type(length) is not int or length <= 0 or not (
                item['start'] <= addr < addr+length <= item['end_exclusive']):
            issue('outside_whitelist', ident, 'range/id/length not permitted')
            checks.append(dict(id=ident, passed=False)); continue
        key = item['id']; occurrences[key] += 1; amounts[key] += length
        if 'max_occurrences' in item and occurrences[key] > item['max_occurrences']:
            issue('max_occurrences', ident, key)
        if type(op.get('occurrence')) is not int or op.get('occurrence') != occurrences[key]:
            issue('occurrence', ident, 'must be consecutive per policy item')
        if ident in seen_ids:
            issue('duplicate_id', ident, 'duplicate operation')
        seen_ids.add(ident)
        stage = op.get('stage')
        if stage not in ORDER:
            issue('order', ident, 'unknown stage')
        else:
            ranks.append(ORDER.index(stage))
        if op.get('direction') != 'write' or op.get('operation') not in ('write', 'rmw'):
            issue('operation_kind', ident, 'only declared memory writes/RMW templates')
        try:
            if item['kind'] == 'volatile_sram':
                need(stage == 'D09' and op.get('operation') == 'write', 'sram_stage', ident)
                off = item['data_rule']['source_offset']+addr-item['start']
                expected = {'kind': 'firmware_slice', 'source_offset': off, 'sha256': digest(fw[off:off+length])}
                need(op.get('data') == expected, 'payload_mismatch', ident)
                intervals[key].append((addr, addr+length))
            else:
                need(ident == stage == key and addr == item['start'] and length == item['end_exclusive']-addr,
                     'register_shape', ident)
                need(op.get('operation') == ('rmw' if item['requires_valid_prior_read'] else 'write'),
                     'rmw_mismatch', ident)
                need(op.get('data') == register_data(fw, item), 'payload_mismatch', ident)
                if item['requires_valid_prior_read']:
                    issue('unresolved_rmw', ident, 'chip prior read is unavailable offline', 'blocker')
            tx = next(x for x in path if x['id'] == stage)
            need(type(op.get('wait_after_ms')) is int and op['wait_after_ms'] == tx['wait_ms'], 'wait_mismatch', ident)
        except Rejected as exc:
            issue(exc.code, ident, str(exc))
        checks.append(dict(id=ident, policy_id=key, address=addr, length=length,
                           passed=len(issues) == before, data_resolved=op.get('data', {}).get('kind') != 'unresolved_prior_read'))
    if ranks != sorted(ranks):
        issue('order', 'schedule', 'D01..D18 writes out of order / D09 not between D08 and D10')
    for key, item in allowed.items():
        if item['kind'] == 'volatile_sram':
            expected = item['start']; valid = True
            for start, end in sorted(intervals[key]):
                valid = valid and start == expected; expected = end
            if not valid or expected != item['end_exclusive'] or amounts[key] != item['max_total_bytes']:
                issue('sram_coverage', key, 'gap/overlap/replay/incomplete partition')
        elif occurrences[key] != 1:
            issue('required_occurrence', key, 'expected exactly one register write')
    # Independent chunk segmentation check: existing policy has byte/replay, not SRAM occurrence limits.
    chunks = derive_chunks(fw, allowed, transactions)
    actual_sram = [(x.get('id'), x.get('address'), x.get('length')) for x in plan['operations'] if x.get('stage') == 'D09']
    if actual_sram != [(f"D09.{x['ordinal']:03}", x['address'], x['length']) for x in chunks]:
        issue('chunk_shape', 'D09', 'not audited segmentation')
    expected_schedule, read_ids = [], []
    for tx in path:
        if tx['direction'] == 'read':
            read_ids.append(tx['id'])
            expected_schedule.append({'kind': 'unapproved_read_dependency', 'id': tx['id']})
        else:
            expected_schedule.extend({'kind': 'write', 'id': x['id']} for x in plan['operations'] if x['stage'] == tx['id'])
    if plan.get('schedule') != expected_schedule:
        issue('order', 'schedule', 'missing/reordered dependency or write')
    reads = plan.get('required_reads', [])
    if [x.get('id') for x in reads] != read_ids:
        issue('read_dependencies', 'schedule', 'not the reviewed read schedule')
    for tx in (x for x in path if x['direction'] == 'read'):
        expected = dict(id=tx['id'], address=tx['address'], length=tx['length'], direction='read',
                        approved=False, repeat=tx.get('repeat'), success=tx['success'], wait_ms=tx['wait_ms'])
        if expected not in reads:
            issue('read_dependencies', tx['id'], 'modified read metadata / attempted approval')
        issue('read_not_allowlisted', tx['id'], 'write whitelist supplies no read authorization/count; not emitted as an operation', 'blocker')
    computed = totals(plan['operations'], allowed)
    if plan.get('totals') != computed:
        issue('totals', 'plan', 'stored totals do not match operations')
    for key, value in EXPECTED_COUNTS.items():
        if computed[key] != value:
            issue('historical_count_mismatch', key, f'{computed[key]} != {value}')
    if plan.get('execution_authorized') is not False or plan.get('status') != 'REJECTED_INCOMPLETE':
        issue('unsafe_status', 'plan', 'must retain offline rejection, never an execution permit')
    if plan.get('input_sha256') != {k: v[1] for k, v in INPUTS.items()}:
        issue('plan_provenance', 'plan', 'input digest binding differs')
    if plan.get('skipped_zero_partitions') != [p['index'] for p in parts if not p['bytes']]:
        issue('zero_partition', 'plan', 'zero entries must be skipped, never emitted')
    policy_checks = []
    for key, item in allowed.items():
        entry = dict(id=key, address=item['start'], end_exclusive=item['end_exclusive'],
                     actual_occurrences=occurrences[key], actual_bytes=amounts[key])
        if item['kind'] == 'volatile_sram':
            entry.update(slice_sha256=digest(slice_bytes(fw, item)),
                         expected_slice_sha256=item['data_rule']['slice_sha256'],
                         max_total_bytes=item['max_total_bytes'], max_replays=item['max_replays'],
                         max_occurrences=None,
                         occurrence_note='not provided in whitelist; exact audited chunks plus no gaps/overlaps/replay')
        else:
            entry.update(max_occurrences=item['max_occurrences'],
                         data_rule_result=register_data(fw, item),
                         firmware_field_offset=item['data_rule'].get('field_file_offset'),
                         field_width_bytes=item['data_rule'].get('field_width_bytes'))
        entry['issues'] = [x for x in issues if x['id'] == key or any(
            op['id'] == x['id'] and op['policy_id'] == key for op in plan['operations'])]
        entry['passed'] = not entry['issues']
        policy_checks.append(entry)
    return dict(schema_version=1, status='REJECTED_INCOMPLETE' if issues else 'VERIFIED_OFFLINE',
                passed=not issues, execution_authorized=False,
                structural_checks_passed=not any(x['severity'] == 'error' for x in issues),
                operations=checks, policy_checks=policy_checks,
                container_partitions=[dict(p, action='include' if p['bytes'] else 'reject_from_writes') for p in parts],
                dependency_check='all five normal-path reads are requirements only, not newly authorized operations',
                order_check=not any(x['code'] == 'order' for x in issues),
                forbidden_check=not any(x['code'].startswith('forbidden:') for x in issues),
                issues=issues, actual=computed, expected_sram=EXPECTED_COUNTS,
                counts_match=all(computed[k] == v for k, v in EXPECTED_COUNTS.items()),
                forbidden_review='pinned semantic categories, known-address denies, exact-value default deny; no MCU semantic proof')


# Bounded-read plan additions. The earlier parser/write validator is preserved;
# only this copy's run location and CLI change. No predecessor imports execute.
legacy_make_plan = make_plan
legacy_validate_plan = validate_plan
APPENDIX_PATH = Path(__file__).with_name('read-allowlist.json')
PREDECESSOR_HASHES = {
    'thp-plan.py': '091a2a82654076042b649e9a85a51dc322993555c8f37a0e40db8f216458b4e8',
    'test-thp-plan.py': '149b267b50673b53564d5c03416f02d3bb552624c15c90f60151f34063e0cd8d',
}
ABORT_POLICY = dict(on_read_error='abort', on_poll_exhaustion='abort', on_read_limit='abort',
                    no_writes_after_abort=True, max_download_attempts=1,
                    reset_or_recovery_after_abort=False)
READ_SPECS = [('R10', 'D10', 260081, 1, 200), ('R11', 'D11', 258830, 1, 1),
              ('R15', 'D15', 258317, 1, 1), ('R17', 'D17', 258317, 1, 1),
              ('R18', 'D18', 196192, 6, 11)]


def appendix_checks(appendix, whitelist, transactions):
    """Normative task bounds AND pinned audit parameters; unknown fields deny."""
    checks, issues = [], []
    def bad(code, ident, detail):
        issues.append(dict(code=code, id=ident, detail=detail, severity='error'))
    expected_keys = {'schema_version', 'experiment', 'execution_authorized', 'transaction_sha256',
                     'allowed_reads', 'total_read_limit', 'rmw_bindings', 'abort_policy'}
    if set(appendix) != expected_keys or appendix.get('schema_version') != 1 or appendix.get('experiment') != 'EXP-0230':
        bad('appendix_schema', 'appendix', 'unknown or missing fields/profile')
    if appendix.get('execution_authorized') is not False:
        bad('authorization_change', 'appendix', 'offline only')
    if appendix.get('transaction_sha256') != INPUTS['transactions'][1]:
        bad('appendix_transaction_hash', 'appendix', 'not bound to pinned audit')
    reads = appendix.get('allowed_reads', [])
    if not isinstance(reads, list) or [x.get('id') for x in reads] != [x[0] for x in READ_SPECS]:
        bad('read_extra_or_missing', 'appendix', 'exactly R10/R11/R15/R17/R18 in order')
    for rid, stage, address, length, budget in READ_SPECS:
        hits = [x for x in reads if x.get('id') == rid]
        if len(hits) != 1:
            checks.append(dict(id=rid, passed=False)); continue
        item = hits[0]; before = len(issues)
        tx = next(x for x in transactions['download'] if x['id'] == stage)
        repeat = tx.get('repeat')
        audit_budget = (repeat.get('max_reads') if stage == 'D10' else
                        repeat['initial_reads']+repeat['additional_reads'] if stage == 'D18' else 1)
        if stage == 'D10':
            condition = dict(kind='equals', index=0, value=0)
            if repeat != dict(max_reads=200, until='RX[0]==0', sleep_after_each_nonzero_ms=1):
                bad('read_audit_repeat', rid, 'D10 audit does not match task rule')
        elif stage == 'D18':
            condition = dict(kind='range_inclusive', index=0, min=160, max=175)
            if repeat['initial_reads'] != 1 or repeat['additional_reads'] != 10 or repeat['interval_ms'] != 10:
                bad('read_audit_repeat', rid, 'D18 audit does not match task rule')
        else:
            condition = dict(kind='transport_success')
        if type(item.get('max_reads')) is not int or item['max_reads'] != budget or audit_budget != budget:
            bad('read_budget_changed', rid, 'budget differs from task/audit')
        if item.get('repeat') != repeat:
            bad('read_repeat_mismatch', rid, 'repeat metadata differs from audit')
        expected = dict(id=rid, stage=stage, address=address, length=length, max_reads=budget,
                        repeat=repeat, success_condition=condition,
                        read_value_consumers={'R11': ['D12'], 'R15': ['D16']}.get(rid, []),
                        record_only=rid == 'R17', on_transport_error='abort', on_exhaustion='abort')
        if (tx['direction'], tx['address'], tx['length']) != ('read', address, length):
            bad('read_audit_address', rid, 'task differs from transaction table')
        if encoded(item) != encoded(expected):
            bad('read_rule_mismatch', rid, 'unknown/missing/mutated read fields')
        checks.append(dict(id=rid, stage=stage, address=address, length=length, max_reads=budget,
                           address_matches=item.get('address') == tx['address'],
                           length_matches=item.get('length') == tx['length'],
                           repeat_matches=item.get('repeat') == repeat,
                           passed=len(issues) == before))
    actual_limit = sum(x['max_reads'] for x in reads if type(x.get('max_reads')) is int)
    if type(appendix.get('total_read_limit')) is not int or appendix['total_read_limit'] != 214 or actual_limit != 214:
        bad('total_read_budget_changed', 'appendix', 'must equal 200+1+1+1+11=214')
    allowed = {x['id']: x for x in whitelist['allowed']}
    bindings = []
    for write, rid, read in [('D12', 'R11', 'D11'), ('D16', 'R15', 'D15')]:
        item = allowed[write]; rule = item['data_rule']
        restriction = next(x for x in whitelist['restrictions']['rmw_only'] if x['address'] == item['start'])
        need(restriction['width'] == 1 and restriction['set_mask'] == rule['set_mask']
             and rule['preserve_mask'] == (255 ^ restriction['set_mask']), 'rmw_mask_conflict', write)
        bindings.append(dict(write_stage=write, read_id=rid, read_stage=read,
                             address=item['start'], width=restriction['width'],
                             preserve_mask=rule['preserve_mask'], set_mask=restriction['set_mask'],
                             same_execution=True, immediate_predecessor=True, single_use=True))
    if encoded(appendix.get('rmw_bindings')) != encoded(bindings):
        bad('rmw_appendix_binding', 'appendix', 'masks/address/fresh-read binding must match write policy')
    if appendix.get('abort_policy') != ABORT_POLICY:
        bad('abort_policy_changed', 'appendix', 'one attempt; sticky abort; no recovery')
    return dict(checks=checks, issues=issues, total_read_limit=actual_limit,
                total_limit_matches=actual_limit == appendix.get('total_read_limit') == 214,
                expected_bindings=bindings)


def symbolic_data(binding):
    return dict(kind='symbolic_rmw', read_id=binding['read_id'], read_stage=binding['read_stage'],
                preserve_mask=binding['preserve_mask'], set_mask=binding['set_mask'],
                same_execution=True, requires_success=True, immediate_predecessor=True, single_use=True)


def read_step(item):
    return dict(id=item['stage'], direction='read', operation='read', rule=item)


def symbolic_totals(steps):
    writes = [x for x in steps if x.get('direction') == 'write']
    sram = [x for x in writes if x.get('stage') == 'D09']
    return dict(step_count=len(steps), write_count=len(writes), read_step_count=len(steps)-len(writes),
                write_payload_bytes=sum(x['length'] for x in writes),
                sram_bytes=sum(x['length'] for x in sram), sram_write_count=len(sram),
                nonempty_partitions=len({x['policy_id'] for x in sram}),
                symbolic_rmw_count=sum(x.get('operation') == 'rmw' for x in writes),
                total_read_limit=214)


def make_symbolic_plan(fw, whitelist, transactions, forbidden, appendix, hashes):
    audit = appendix_checks(appendix, whitelist, transactions)
    need(not audit['issues'], audit['issues'][0]['code'] if audit['issues'] else 'appendix',
         str(audit['issues']))
    legacy = legacy_make_plan(fw, whitelist, transactions, forbidden, {k: hashes[k] for k in INPUTS})
    operations = {x['id']: x for x in legacy['operations']}
    reads = {x['stage']: x for x in appendix['allowed_reads']}
    bindings = {x['write_stage']: x for x in audit['expected_bindings']}
    steps = []
    for node in legacy['schedule']:
        if node['kind'] == 'unapproved_read_dependency':
            steps.append(read_step(reads[node['id']]))
        else:
            op = dict(operations[node['id']])
            if op['id'] in bindings:
                op['data'] = symbolic_data(bindings[op['id']])
            steps.append(op)
    return dict(schema_version=2, experiment='EXP-0230', execution_authorized=False,
                status='COMPLETE_OFFLINE_SYMBOLIC', input_sha256=hashes,
                scope='logical symbolic memory operations only; no wire executor or real chip evidence',
                steps=steps, totals=symbolic_totals(steps), abort_policy=appendix['abort_policy'],
                skipped_zero_partitions=legacy['skipped_zero_partitions'])


def validate_symbolic_plan(plan, fw, whitelist, transactions, forbidden, appendix, hashes):
    audit = appendix_checks(appendix, whitelist, transactions)
    issues = list(audit['issues'])
    def bad(code, ident, detail):
        issues.append(dict(code=code, id=ident, detail=detail, severity='error'))
    expected = legacy_make_plan(fw, whitelist, transactions, forbidden, {k: hashes[k] for k in INPUTS})
    steps = plan.get('steps', [])
    expected_ids = [x['id'] for x in expected['schedule']]
    if [x.get('id') for x in steps] != expected_ids:
        bad('step_order', 'plan', 'full D01..D18 order including immediate read->RMW is required')
    binding_map = {x['write_stage']: x for x in audit['expected_bindings']}
    reads = {x['stage']: x for x in appendix.get('allowed_reads', [])}
    projected_writes, projected_schedule = [], []
    for index, step in enumerate(steps):
        ident = step.get('id', '<missing>')
        if step.get('direction') == 'read':
            if ident not in reads or step != read_step(reads[ident]):
                bad('read_step_mismatch', ident, 'not an exact read appendix step')
            projected_schedule.append(dict(kind='unapproved_read_dependency', id=ident))
            continue
        op = dict(step)
        if ident in binding_map:
            binding = binding_map[ident]
            data = op.get('data', {})
            if data.get('kind') != 'symbolic_rmw':
                bad('rmw_fixed_value', ident, 'RMW must depend on the fresh read, not a constant')
            elif encoded(data) != encoded(symbolic_data(binding)):
                bad('rmw_read_binding', ident, 'wrong read id/stage/masks/freshness rule')
            prior = steps[index-1] if index else {}
            if prior.get('id') != binding['read_stage'] or prior != read_step(reads.get(binding['read_stage'], {})):
                bad('rmw_not_adjacent', ident, 'immediately preceding same-address read is required')
            old_op = next(x for x in expected['operations'] if x['id'] == ident)
            op['data'] = old_op['data']  # project only to recheck unchanged write policy
        elif op.get('data', {}).get('kind') == 'symbolic_rmw' or any(
                k.startswith('read_') for k in op.get('data', {})):
            bad('read_value_outside_rmw', ident, 'only D12 and D16 may consume a read value')
        projected_writes.append(op)
        projected_schedule.append(dict(kind='write', id=ident))
    projection = dict(expected, operations=projected_writes, schedule=projected_schedule)
    projection['totals'] = totals(projected_writes, {x['id']: x for x in whitelist['allowed']})
    legacy_result = legacy_validate_plan(projection, fw, whitelist, transactions, forbidden)
    # Only the seven predecessor blockers resolved by this explicitly checked appendix are removed.
    issues.extend(x for x in legacy_result['issues'] if x['code'] not in ('unresolved_rmw', 'read_not_allowlisted'))
    if plan.get('totals') != symbolic_totals(steps):
        bad('totals', 'plan', 'step counts/bytes differ')
    if plan.get('abort_policy') != ABORT_POLICY:
        bad('abort_policy_changed', 'plan', 'sticky abort required')
    expected_keys = {'schema_version', 'experiment', 'execution_authorized', 'status', 'input_sha256',
                     'scope', 'steps', 'totals', 'abort_policy', 'skipped_zero_partitions'}
    if set(plan) != expected_keys or plan.get('schema_version') != 2 or plan.get('experiment') != 'EXP-0230':
        bad('plan_schema', 'plan', 'unknown profile/fields')
    if plan.get('execution_authorized') is not False or plan.get('status') != 'COMPLETE_OFFLINE_SYMBOLIC':
        bad('unsafe_status', 'plan', 'only offline symbolic completion, execution always false')
    if plan.get('input_sha256') != hashes or plan.get('skipped_zero_partitions') != expected['skipped_zero_partitions']:
        bad('plan_provenance', 'plan', 'changed input binding or zero partitions')
    return dict(schema_version=2, status='REJECTED_INCOMPLETE' if issues else 'COMPLETE_OFFLINE_SYMBOLIC',
                passed=not issues, execution_authorized=False, issues=issues, actual=symbolic_totals(steps),
                read_appendix_checks=audit['checks'], total_read_limit_check=audit['total_limit_matches'],
                total_read_limit=audit['total_read_limit'], rmw_bindings=audit['expected_bindings'],
                write_policy_structural_checks_passed=legacy_result['structural_checks_passed'],
                counts_match=legacy_result['counts_match'], predecessor_sha256=PREDECESSOR_HASHES,
                resolved_predecessor_blockers=['D10', 'D11', 'D12', 'D15', 'D16', 'D17', 'D18'],
                limits='symbolic host validation only; no real read values or MCU/display safety proof')


def read_inputs(paths=None, appendix_path=APPENDIX_PATH):
    raw, checks = {}, []
    paths = paths or {k: RUN/'inputs'/name for k, (name, _) in INPUTS.items()}
    for key, (_, expected) in INPUTS.items():
        raw[key] = regular_bytes(paths[key])
        checks.append(dict(id=key, size=len(raw[key]), sha256=digest(raw[key]), expected=expected,
                           passed=digest(raw[key]) == expected))
    need(all(x['passed'] for x in checks), 'input_sha256', str(checks))
    raw_appendix = regular_bytes(appendix_path)
    hashes = {x['id']: x['sha256'] for x in checks}
    hashes['read_allowlist'] = digest(raw_appendix)
    return dict(fw=raw['firmware'], whitelist=load_json(raw['whitelist']),
                transactions=load_json(raw['transactions']), forbidden=load_json(raw['forbidden']),
                appendix=load_json(raw_appendix), hashes=hashes), checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key, (name, _) in INPUTS.items():
        parser.add_argument('--'+key, type=Path, default=RUN/'inputs'/name)
    parser.add_argument('--read-allowlist', type=Path, default=APPENDIX_PATH)
    parser.add_argument('--output-dir', type=Path, default=RUN/'run-a')
    parser.add_argument('--check-plan', type=Path)
    args = parser.parse_args(argv); out = args.output_dir.resolve()
    try:
        need(out.is_relative_to(RUN.resolve()), 'output_scope', 'output must stay inside local/touch-plan/')
        out.mkdir(parents=True, exist_ok=True)
        need(not any((out/n).exists() for n in ('plan.json', 'verification.json')), 'output_exists', 'no overwrites')
        bundle, checks = read_inputs({k: getattr(args, k) for k in INPUTS}, args.read_allowlist)
        appendix_audit = appendix_checks(bundle['appendix'], bundle['whitelist'], bundle['transactions'])
        if appendix_audit['issues']:
            result = dict(status='REJECTED_INCOMPLETE', passed=False, execution_authorized=False,
                          inputs=checks, read_appendix_checks=appendix_audit)
            (out/'verification.json').write_bytes(encoded(result)); print('REJECTED_INCOMPLETE: read appendix', file=sys.stderr)
            return 2
        plan = load_json(regular_bytes(args.check_plan)) if args.check_plan else make_symbolic_plan(**bundle)
        result = validate_symbolic_plan(plan, **bundle); result['inputs'] = checks
        result['read_allowlist_sha256'] = bundle['hashes']['read_allowlist']
        plan['status'] = result['status']
        data = encoded(plan); result['plan_sha256'] = digest(data)
        (out/'plan.json').write_bytes(data); (out/'verification.json').write_bytes(encoded(result))
        print(json.dumps(dict(status=result['status'], actual=result['actual'], plan_sha256=result['plan_sha256'])))
        return 0 if result['passed'] else 2
    except (Rejected, OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        if out.is_relative_to(RUN.resolve()) and out.is_dir() and not (out/'verification.json').exists():
            (out/'verification.json').write_bytes(encoded(dict(status='REJECTED_INCOMPLETE', passed=False,
                execution_authorized=False, code=getattr(exc, 'code', type(exc).__name__), error=str(exc))))
        print(str(exc), file=sys.stderr); return 2


if __name__ == '__main__':
    sys.exit(main())
