#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rebuild the pinned historical audit from an owner's AFE file, offline only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AFE_SHA = '4d3e05edc9a69769e1af20e1560ff88458229898191d90f33bd90d6eaff17bb0'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(ok, message):
    if not ok:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--afe', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--objdump', default='aarch64-linux-gnu-objdump')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        require(args.afe.is_file() and not args.afe.is_symlink(), 'AFE must be a regular, non-symlink file')
        require(args.afe.stat().st_size == 624352, 'AFE size mismatch')
        afe = args.afe.read_bytes()
        require(hashlib.sha256(afe).hexdigest() == AFE_SHA, 'AFE SHA-256 mismatch; unsupported input')
        out = args.output_dir.resolve()
        base = (ROOT / 'local/touch-plan').resolve()
        require(out.is_relative_to(base) and out != base, 'output must be a new directory below local/touch-plan/')
        require(not out.exists() and not args.output_dir.is_symlink(), 'output already exists')
        version = subprocess.check_output([args.objdump, '--version'], text=True).splitlines()[0]
        require(version.startswith('GNU objdump') and version.endswith(' 2.46'), 'requires GNU objdump 2.46 for exact audit formatting')
        plan = load('private_plan', HERE / 'thp-plan.py')
        gen = load('private_header', HERE / 'gen-plan-header.py')
        fw = afe[0x2a5d4:0x2a5d4 + 0x30000]
        require(plan.digest(fw) == gen.PINS['stock-nt36523-thp.bin'], 'firmware SHA-256 mismatch')
        parts = [dict(p, source_in_bounds=not p['size_field'] or p['source']+p['bytes'] <= len(fw),
                      below_event_sram_limit=not p['size_field'] or p['sram']+p['bytes'] <= 0x2fe00)
                 for p in plan.parse_container(fw)]
        out.mkdir(parents=True, mode=0o700)
        audit = out / 'audit'
        (audit / 'inputs').mkdir(parents=True)
        (audit / 'evidence').mkdir()
        (audit / 'inputs/afe-p.so').write_bytes(afe)
        (audit / 'inputs/stock-thp.bin').write_bytes(fw)
        (audit / 'inputs/firmware-layout.json').write_text(json.dumps(dict(
            parent_sha256=AFE_SHA, sha256=plan.digest(fw), parts=parts)))
        env = dict(os.environ, LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')
        with (audit / 'evidence/afe.disassembly.txt').open('xb') as stream:
            subprocess.run([args.objdump, '-d', str(audit / 'inputs/afe-p.so')],
                           stdout=stream, check=True, env=env)
        for script in ('index-calls.py', 'build-audit.py'):
            subprocess.run([sys.executable, '-B', '-I', str(HERE / 'plan-tools' / script), str(audit)],
                           check=True, env=env)
        inputs = out / 'inputs'
        inputs.mkdir()
        (inputs / 'stock-nt36523-thp.bin').write_bytes(fw)
        for name in ('write-whitelist.json', 'spi-transactions.json', 'forbidden.json'):
            data = (audit / name).read_bytes()
            require(plan.digest(data) == gen.PINS[name], 'audit SHA-256 mismatch: '+name)
            (inputs / name).write_bytes(data)
        tx = json.loads((inputs / 'spi-transactions.json').read_text())
        wl = json.loads((inputs / 'write-whitelist.json').read_text())
        reads = []
        for rid, stage, address, length, budget in plan.READ_SPECS:
            entry = next(x for x in tx['download'] if x['id'] == stage)
            condition = (dict(kind='equals', index=0, value=0) if stage == 'D10' else
                         dict(kind='range_inclusive', index=0, min=160, max=175) if stage == 'D18' else
                         dict(kind='transport_success'))
            reads.append(dict(id=rid, stage=stage, address=address, length=length, max_reads=budget,
                              repeat=entry.get('repeat'), success_condition=condition,
                              read_value_consumers={'R11':['D12'], 'R15':['D16']}.get(rid, []),
                              record_only=rid == 'R17', on_transport_error='abort', on_exhaustion='abort'))
        bindings = []
        for write, rid, read in [('D12','R11','D11'), ('D16','R15','D15')]:
            entry = next(x for x in wl['allowed'] if x['id'] == write)
            bindings.append(dict(write_stage=write, read_id=rid, read_stage=read, address=entry['start'],
                                 width=1, preserve_mask=entry['data_rule']['preserve_mask'],
                                 set_mask=entry['data_rule']['set_mask'], same_execution=True,
                                 immediate_predecessor=True, single_use=True))
        appendix = dict(schema_version=1, experiment='EXP-0230', execution_authorized=False,
                        transaction_sha256=gen.PINS['spi-transactions.json'], allowed_reads=reads,
                        total_read_limit=214, rmw_bindings=bindings, abort_policy=plan.ABORT_POLICY)
        data = plan.encoded(appendix)
        require(plan.digest(data) == gen.PINS['read-allowlist.json'], 'read appendix SHA-256 mismatch')
        (inputs / 'read-allowlist.json').write_bytes(data)
        shutil.copyfile(HERE / 'plan-tools/nt36523-format.h', inputs / 'nt36523-format.h')
        cmd = [sys.executable, '-B', '-I', str(HERE / 'thp-plan.py'), '--output-dir', str(inputs),
               '--read-allowlist', str(inputs / 'read-allowlist.json')]
        for key, (name, _) in plan.INPUTS.items():
            cmd += ['--'+key, str(inputs / name)]
        subprocess.run(cmd, check=True, env=env)
        raw, checked_plan, bundle = gen.load_inputs(inputs)
        gen.rows_for(checked_plan, bundle['fw'])
        (out / 'preparation.json').write_text(json.dumps(dict(status='VERIFIED_OFFLINE',
            objdump=version, afe_sha256=AFE_SHA, input_sha256={n:plan.digest(b) for n,b in raw.items()},
            hardware_tested=False), sort_keys=True, indent=2)+'\n')
        print('VERIFIED_OFFLINE: '+str(inputs))
        return 0
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
