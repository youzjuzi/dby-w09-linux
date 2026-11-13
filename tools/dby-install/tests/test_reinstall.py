# SPDX-License-Identifier: GPL-2.0-only
"""p2-only destructive operations on regular images and injected SD adapters."""
from contextlib import contextmanager
import io
import json
import os
import shutil
import hashlib
import tempfile
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(TOOL))
from dbyinstall import reinstall as R, core, image, privileged, maintenance, seed
from dbyinstall.common import BundleError, MIN_SIZE, P1_START, P1_SECTORS, P2_START
from dbyinstall.maintenance_io import region_hash, replace_region
from dbyinstall.seed_io import SeedBackend
from dbyinstall.targets import BlockDeviceTarget, live_probe
import test_maintenance as fixtures
import test_hardening as hardening
from test_install import probe_row


class ReinstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.MaintenanceTests.setUpClass.__func__(cls)
    def setUp(self): fixtures.MaintenanceTests.setUp(self)

    def operate(self, **kwargs):
        options=dict(minimal=True, ask=lambda _: str(self.target.path)+' p2', emit=self.messages.append)
        options.update(kwargs)
        return R.operate(self.target, self.old, **options)

    def mutate(self, function):
        with SeedBackend(self.target) as backend, backend.locked(), backend.transaction() as store: function(store)

    def dirty_root(self):
        source=self.root/'marker';source.write_bytes(b'old-user-data')
        return hardening.HardeningTests.edit_p2(self,'mkdir /home','mkdir /home/fixture','write '+image.quote(source)+' /home/fixture/old','write '+image.quote(source)+' /old-marker')

    def test_minimal_and_full_reinstall_remove_user_data_preserve_p1_table_seed(self):
        with self.target.path.open('rb') as stream: table=stream.read(512)
        for minimal in (True,False):
            self.dirty_root()
            result=self.operate(minimal=minimal)
            with image.opened(self.target) as (p1,p2,filesystem):
                self.assertFalse((p2/'old-marker').exists());self.assertFalse((p2/'home/fixture/old').exists())
                self.assertTrue((p2/'usr/bin/fixture').is_file())
                core.verify_p2_contract(p2,'test' if minimal else 'full')
                self.assertEqual((p1/'dby-seed/seed.conf').read_bytes(),b'synthetic private seed\n')
            self.assertEqual(region_hash(self.target.path,P1_START*512,P1_SECTORS*512),self.original_p1)
            with self.target.path.open('rb') as f:self.assertEqual(f.read(512),table)
            self.assertEqual(result['p2']['size_bytes'],1024**3)
        self.assertTrue(any('known_hosts' in m for m in self.messages))

    def test_capacity_uses_existing_partition_and_refuses_without_writes(self):
        original=core.prepare
        def large(*a,**k):
            doc,entries,_=original(*a,**k);return doc,entries,1024**3
        with patch.object(core,'prepare',side_effect=large),self.assertRaisesRegex(BundleError,'E_REINSTALL_P2_TOO_SMALL.*install'):
            self.operate(minimal=False)
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),self.original_disk)

    def test_bundle_mismatch_same_id_and_different_id_requires_update(self):
        for sequence in (1,2):
            other=fixtures.changed_bundle(self.root/('bundle-'+str(sequence)),sequence=sequence)
            with self.assertRaisesRegex(BundleError,'E_REINSTALL_BUNDLE_MISMATCH.*update'):
                R.operate(self.target,other,dry_run=True)
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),self.original_disk)

    def test_manifest_missing_and_inconsistent_refuse_without_writes(self):
        source=self.root/'bad';source.write_bytes(b'changed')
        self.mutate(lambda s:(s.remove('UBOOT/Image'),s.copy(source,'UBOOT/Image')))
        for missing in (False,True):
            if missing:self.mutate(lambda s:s.remove('manifest.json'))
            before=region_hash(self.target.path,0,MIN_SIZE)
            with self.assertRaisesRegex(BundleError,'E_REINSTALL_UNMANAGED' if missing else 'E_REINSTALL_CARD_INCONSISTENT'):
                self.operate()
            self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),before)

    def test_every_staging_name_refused_without_writes(self):
        source=self.root/'pending';source.write_bytes(b'pending')
        for name in ('UBOOT.new','UBOOT.old','manifest.json.new','dby-seed/anything.new'):
            self.mutate(lambda s:s.copy(source,name))
            before=region_hash(self.target.path,0,MIN_SIZE)
            with self.assertRaisesRegex(BundleError,'E_SEED_LEFTOVER' if name.startswith('dby-seed') else 'E_UPDATE_LEFTOVER'):
                self.operate()
            self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),before)
            self.mutate(lambda s:s.remove(name))

    def test_confirm_suffix_and_dry_run(self):
        for value in (str(self.target.path),'yes',str(self.root/'wrong.img')+' p2'):
            with self.assertRaisesRegex(BundleError,'E_TARGET_CONFIRM'): self.operate(ask=lambda _:value)
        result=self.operate(dry_run=True,ask=lambda _:self.fail('dry-run asked'))
        self.assertEqual(result['confirmation'],str(self.target.path)+' p2')
        cli=subprocess.run([sys.executable,'-I','-B',str(TOOL/'dby-install'),'reinstall-system','--image-file',str(self.target.path),'--bundle',str(self.old),'--minimal','--dry-run'],capture_output=True)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual(json.loads(cli.stdout),result)
        self.assertIn('home',result['warning']);self.assertIn('backup',result['backup_notice'])
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),self.original_disk)

    def test_dirty_journal_is_reformatted(self):
        hardening.HardeningTests.edit_p2(self,'feature needs_recovery')
        self.operate()
        with image.opened(self.target) as (_,_,part):
            self.assertNotIn(b'needs_recovery',image.run(['debugfs','-R','features',part]).split())

    def test_extraction_failure_is_incomplete_and_still_checks_p1_and_cleans(self):
        workspaces=[];original=R.Backend.immutable
        def fail(*a,**k):raise BundleError('E_SYNTHETIC','private detail withheld')
        def remember(backend):workspaces.append(backend.work);return original(backend)
        with patch.object(core,'extract_rootfs',side_effect=fail),patch.object(R.Backend,'immutable',autospec=True,side_effect=remember) as check:
            with self.assertRaisesRegex(BundleError,'E_REINSTALL_FAILED.*p2 is incomplete') as caught:self.operate()
        self.assertNotIn('rolled back',str(caught.exception));self.assertNotIn('private detail',str(caught.exception))
        self.assertIn('p1 and table hashes verified',str(caught.exception));self.assertGreaterEqual(check.call_count,3)
        self.assertTrue(all(not p.exists() for p in workspaces))
        self.assertEqual(region_hash(self.target.path,P1_START*512,P1_SECTORS*512),self.original_p1)
        with image.opened(self.target) as (_,p2,_):self.assertFalse((p2/'usr/bin/fixture').exists())

    def test_readback_corruption_fails_but_no_readback_skips_file_hashes(self):
        def corrupt(phase,backend):
            if phase=='before_readback':
                source=self.root/'corrupt';source.write_bytes(b'corrupt')
                hardening.HardeningTests.edit_p2(self,'rm /usr/bin/fixture','write '+image.quote(source)+' /usr/bin/fixture')
        with self.assertRaisesRegex(BundleError,'E_REINSTALL_FAILED.*E_INSTALL_HASH'):self.operate(hook=corrupt)
        self.operate(hook=corrupt,no_readback=True)
        self.assertTrue(any('verify' in m for m in self.messages))
        self.assertEqual(region_hash(self.target.path,P1_START*512,P1_SECTORS*512),self.original_p1)

    def test_no_readback_still_checks_contract_and_geometry(self):
        for command in ('rm /etc/fstab','set_super_value volume_name wrong'):
            def corrupt(phase,backend):
                if phase=='before_readback':hardening.HardeningTests.edit_p2(self,command)
            with self.assertRaisesRegex(BundleError,'E_REINSTALL_FAILED'):self.operate(hook=corrupt,no_readback=True)

    def test_update_then_reinstall_and_seed_interoperation(self):
        with self.assertRaisesRegex(BundleError,'E_REINSTALL_BUNDLE_MISMATCH'):R.operate(self.target,self.new,dry_run=True)
        maintenance.operate('update',self.target,bundle=self.new,backup_dir=self.backups,ask=lambda _:str(self.target.path),emit=self.messages.append)
        seed.operate('set',self.target,changes={'refresh_hz':'60'},backup_dir=self.backups,ask=lambda _:str(self.target.path),emit=self.messages.append)
        before=region_hash(self.target.path,P1_START*512,P1_SECTORS*512)
        R.operate(self.target,self.new,ask=lambda _:str(self.target.path)+' p2',emit=self.messages.append)
        self.assertEqual(region_hash(self.target.path,P1_START*512,P1_SECTORS*512),before)

    def test_cli_requires_bundle_and_refuses_unrelated_options(self):
        base=[sys.executable,'-I','-B',str(TOOL/'dby-install'),'reinstall-system','--image-file',str(self.target.path)]
        for options in ([],['--bundle',str(self.old),'--p2-size','1G'],['--bundle',str(self.old),'--image-size','8G'],['--bundle',str(self.old),'--yes']):
            result=subprocess.run(base+options,capture_output=True)
            self.assertEqual(result.returncode,2)
        result=subprocess.run([*base[:5],'--device','/dev/mmcblk8','--bundle',str(self.old),'--image-size','8G'],capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),self.original_disk)

    def test_target_gates_non_sd_system_holder_and_p2_mount(self):
        sysroot=self.root/'sys';node=sysroot/'class/block/mmcblk8'
        for name,content in [('dev','179:0'),('size',str(MIN_SIZE//512)),('removable','0'),('device/type','MMC'),('holders/dm-test/dev','253:0'),('mmcblk8p2/dev','179:2'),('mmcblk8p2/partition','2')]:
            p=node/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
        mounts=self.root/'mountinfo';mounts.write_text('');original=Path.lstat
        def lstat(p):return SimpleNamespace(st_mode=stat.S_IFBLK|0o600,st_rdev=os.makedev(179,0)) if str(p)=='/dev/mmcblk8' else original(p)
        def probe(p):return live_probe(p,sysroot=sysroot,mountinfo=mounts,execute=lambda _:json.dumps({'blockdevices':[dict(probe_row(),path=str(p))]}).encode())
        for media,contents,code in [('MMC','','E_TARGET_MEDIA'),('SD','1 0 253:0 / / rw - ext4 fixture rw\n','E_TARGET_MOUNTED'),('SD','1 0 179:2 / /media/p2 rw - ext4 fixture rw\n','E_TARGET_MOUNTED')]:
            (node/'device/type').write_text(media);mounts.write_text(contents)
            with patch('dbyinstall.targets.no_links'),patch.object(Path,'lstat',lstat),self.assertRaisesRegex(BundleError,code):
                R.operate(BlockDeviceTarget(Path('/dev/mmcblk8'),probe),self.old,minimal=True,dry_run=True)

    def test_fake_sd_reinstall_and_failure_use_only_p2_writes(self):
        import dbyinstall.seed_io as sio
        from dbyinstall.archive import verify_root as real_verify
        for fail in (False,True):
            folder=self.root/str(fail);folder.mkdir();p1=folder/'p1';p2=folder/'p2';p1.mkdir();p2.mkdir()
            core.populate_p1(p1,self.old);(p2/'old-user-data').write_bytes(b'old')
            targetfile=folder/'card8';targetfile.touch()
            target=SimpleNamespace(path=targetfile,validate=lambda:{'size':MIN_SIZE,'rdev':0})
            events=[];mounted=set();commands=[]
            @contextmanager
            def p1mount(device,*,writable=False):
                self.assertFalse(writable);mounted.add(p1);events.append('p1-ro')
                try:yield p1
                finally:mounted.remove(p1)
            @contextmanager
            def p2mount(device,*,readonly=False):
                self.assertFalse(mounted);mounted.add(p2);events.append('p2-ro' if readonly else 'p2-rw')
                try:yield p2
                finally:mounted.remove(p2)
            def execute(argv):
                commands.append(list(map(str,argv)))
                if argv[0]=='tar':
                    self.assertIn('--same-owner',argv);self.assertIn('--xattrs',argv)
                    # Ordinary temporary-directory extraction without chown/root.
                    adapted=['--no-same-owner' if a=='--same-owner' else a for a in argv]
                    image.run(adapted)
                    if fail:raise BundleError('E_SYNTHETIC','private')
                return b''
            def format_only_p2(path):
                self.assertEqual(str(path),str(targetfile)+'p2');self.assertFalse(mounted)
                events.append('mkfs-p2');shutil.rmtree(p2);p2.mkdir()
            def immutable(backend):
                digest=hashlib.sha256()
                for path in sorted(p1.rglob('*')):
                    if path.is_file():digest.update(path.read_bytes())
                return {'table_sha256':'unchanged-table','p1_sha256':digest.hexdigest()}
            def confirm(_):self.assertFalse(mounted);return str(targetfile)+' p2'
            with patch.object(sio,'p1_mount',side_effect=p1mount),patch.object(R,'root_mount',side_effect=p2mount),patch.object(sio,'run',return_value=b'table'),patch.object(R,'run',side_effect=execute),patch.object(privileged,'require_root'),patch.object(privileged,'check_table',return_value=1024**3//512),patch.object(image,'geometry'),patch.object(privileged,'format_rootfs',side_effect=format_only_p2),patch.object(R.Backend,'immutable',autospec=True,side_effect=immutable) as hashes,patch.object(R,'verify_root',side_effect=lambda root,entries:real_verify(root,entries,ownership=False)):
                if fail:
                    with self.assertRaisesRegex(BundleError,'E_REINSTALL_FAILED.*p2 is incomplete'):
                        R.operate(target,self.old,minimal=True,ask=confirm,emit=self.messages.append)
                else:R.operate(target,self.old,minimal=True,ask=confirm,emit=self.messages.append)
            self.assertFalse(mounted);self.assertGreaterEqual(hashes.call_count,3)
            self.assertEqual(events.count('mkfs-p2'),1);self.assertIn('p1-ro',events);self.assertIn('p2-rw',events)
            self.assertFalse((p2/'old-user-data').exists())
            self.assertFalse(any(row[0] in ('sfdisk','mkfs.vfat') for row in commands))


class ReinstallAdapterTests(unittest.TestCase):
    def test_mount_options_are_p1_readonly_and_p2_only_writable(self):
        import dbyinstall.seed_io as sio
        calls=[];mounted=set()
        def execute(argv):
            calls.append(list(map(str,argv)))
            if argv[0]=='mount':mounted.add(Path(argv[-1]))
            elif argv[0]=='umount':mounted.remove(Path(argv[-1]))
            return b''
        with patch.object(sio,'run',side_effect=execute),patch.object(R,'run',side_effect=execute),patch.object(privileged,'mounted_paths',side_effect=lambda:set(mounted)):
            with sio.p1_mount(Path('/dev/mmcblk8')):pass
            with R.root_mount(Path('/dev/mmcblk8')):pass
            with R.root_mount(Path('/dev/mmcblk8'),readonly=True):pass
        self.assertFalse(mounted)
        mounts=[c for c in calls if c[0]=='mount']
        self.assertEqual([(c[-2],c[4].split(',')[0]) for c in mounts],[('/dev/mmcblk8p1','ro'),('/dev/mmcblk8p2','rw'),('/dev/mmcblk8p2','ro')])

    def test_shared_extraction_preserves_install_backend_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            for ownership in (False,True):
                core.extract_rootfs(root,'archive',root,ownership=ownership,execute=lambda argv:calls.append(argv))
                self.assertIn('--same-owner' if ownership else '--no-same-owner',calls[-1])
                self.assertEqual('--xattrs' in calls[-1],ownership)
                self.assertEqual('--acls' in calls[-1],ownership)
                self.assertIn('--same-permissions',calls[-1])
