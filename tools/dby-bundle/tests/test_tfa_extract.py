"""SYNTHETIC, not vendor data: format, policy and refusal tests."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

TOOL = Path(__file__).resolve().parents[2]/'tfa9874-extract-tables.py'
spec = importlib.util.spec_from_file_location('tfa_extract', TOOL)
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


def synthetic(index=0):
    root = ET.Element('NV_CATEGORY', NAME='SYNTHETIC', MODE=str(index))
    status = ET.SubElement(root, 'PA_STATUS', NAME='FAKE')
    for name, count, base in [('INIT_CHIP', 42, 128), ('POWER_ON_L', 9, 192), ('POWER_OFF_L', 6, 208)]:
        rows = []
        for i in range(count):
            reg = base+i
            if name == 'INIT_CHIP' and i in (3, 8):
                reg = (0x02, 0x61)[i == 8]
            rows.append((reg, 65535, 4096+i, 0, 0))
        ET.SubElement(status, 'SCENCE', NAME=name, CONTROL_MASK='1',
                      VALUE=','.join(str(n) for row in rows for n in row))
    return ET.tostring(root)


class ExtractTables(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = [synthetic(i) for i in range(4)]
        self.digests = tuple(extract.parse(b)[1] for b in self.data)
        for i, data in enumerate(self.data):
            (self.root/f'tfa9874_{i}_configs.xml').write_bytes(data)

    def load(self):
        # Synthetic fingerprints are injected in the test process only.
        # The production command has no fingerprint override or fixture mode.
        with patch.object(extract, 'FINGERPRINTS', self.digests):
            return extract.load_inputs(self.root)

    def test_parse_synthetic(self):
        scenes, digest = extract.parse(self.data[0])
        self.assertEqual([len(s) for s in scenes.values()], [42, 9, 6])
        self.assertRegex(digest, r'^[a-f0-9]{64}$')
        self.assertEqual(len(extract.FINGERPRINTS), 4)
        self.assertFalse(set(self.digests) & set(extract.FINGERPRINTS))

    def test_normalization(self):
        root = ET.fromstring(self.data[0])
        for node in root.iter('SCENCE'):
            node.set('VALUE', ' # fake comment # '+', '.join(hex(int(s)) for s in node.get('VALUE').split(',')))
            node.set('CONTROL_MASK', '0x01')
        ET.indent(root)
        self.assertEqual(extract.parse(ET.tostring(root))[1], self.digests[0])

    def test_every_input_is_pinned(self):
        for i in range(4):
            p=self.root/f'tfa9874_{i}_configs.xml'
            p.write_bytes(self.data[i].replace(b'4097', b'4098', 1))
            with self.assertRaises(extract.InvalidInput): self.load()
            p.write_bytes(self.data[i])

    def test_all_scenes_and_metadata_pinned(self):
        for field, value in [('MODE', '999'), ('NAME', 'CHANGED')]:
            root=ET.fromstring(self.data[0]);root.set(field,value)
            self.assertNotEqual(extract.parse(ET.tostring(root))[1], self.digests[0])
        root=ET.fromstring(self.data[0]);list(root.iter('SCENCE'))[-1].set('CONTROL_MASK','2')
        self.assertNotEqual(extract.parse(ET.tostring(root))[1], self.digests[0])

    def test_missing_and_swapped_inputs(self):
        a=self.root/'tfa9874_0_configs.xml';b=self.root/'tfa9874_1_configs.xml'
        a.write_bytes(self.data[1]);b.write_bytes(self.data[0])
        with self.assertRaises(extract.InvalidInput): self.load()
        a.unlink()
        with self.assertRaises(extract.InvalidInput): self.load()

    def test_malformed_and_out_of_range(self):
        cases=[b'<broken', b'<!DOCTYPE x [<!ENTITY a "x">]><NV_CATEGORY/>',
               self.data[0].replace(b'4096', b'65536', 1),
               self.data[0].replace(b'4096', b'FAKE_SECRET', 1),
               self.data[0].replace(b'65535', b'-1', 1),
               self.data[0].replace(b'128,65535', b'256,65535', 1)]
        for data in cases:
            with self.assertRaises(extract.InvalidInput): extract.parse(data)

    def test_duplicate_scene_refused(self):
        root=ET.fromstring(self.data[0]);nodes=list(root.iter('SCENCE'))
        nodes[1].set('NAME',nodes[0].get('NAME'))
        with self.assertRaises(extract.InvalidInput): extract.parse(ET.tostring(root))

    def test_policies_and_derivation(self):
        inputs=self.load();init,off,on=extract.build_tables(inputs)
        self.assertEqual((len(init),len(off),len(on)),(44,6,11))
        self.assertEqual(init[:2],init[3:5])
        self.assertEqual(init[2][3],2)
        for reg,mask,val,delay,kind in init:
            if reg==0x61:
                self.assertEqual((val>>6)&15,15)
                self.assertEqual(val&0x3f,inputs[0]['INIT_CHIP'][8][2]&0x3f)
            if reg==0x02:self.assertEqual(val,0x22d8)
        self.assertEqual(off,inputs[0]['POWER_OFF_L'])
        self.assertEqual(on[:2],[init[5],init[39]])
        self.assertEqual(on[2:-1],inputs[0]['POWER_ON_L'][:-1])
        self.assertEqual(on[-1],init[41])

    def test_render_and_compile(self):
        content=extract.render(self.load())
        self.assertIn(extract.BANNER, content)
        self.assertEqual(len(re.findall(r'^ \{',content,re.M)),61)
        (self.root/extract.HEADER_NAME).write_text(content)
        source=self.root/'test.c'
        source.write_text('#include <stdint.h>\nstruct dby_tfa9874_op { uint16_t reg,mask,val,delay_ms; uint8_t type; };\n'
                          '#include "dby-tfa9874-tables-private.h"\n'
                          'int main(void){struct dby_tfa9874_op on[]=DBY_TFA_ON_INITIALIZER(1,2,3);'
                          'return sizeof(on)/sizeof(on[0])!=11 || sizeof(dby_tfa_init)/sizeof(dby_tfa_init[0])!=44 || '
                          'sizeof(dby_tfa_off)/sizeof(dby_tfa_off[0])!=6;}\n')
        subprocess.run(['cc','-Wall','-Wextra','-Werror',str(source),'-o',str(self.root/'test')],check=True)
        subprocess.run([str(self.root/'test')],check=True)

    def test_command_rejects_fake_and_override_without_echo(self):
        marker='FAKE_SECRET_DO_NOT_ECHO'
        for extra in [[],['--fingerprint',marker],['--input-dir',marker]]:
            result=subprocess.run(['python3','-I','-B',str(TOOL),'--input-dir',str(self.root),
                                   '--output',str(self.root/extract.HEADER_NAME),*extra],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn(marker,result.stdout+result.stderr)
            self.assertNotIn(str(self.root),result.stdout+result.stderr)
            self.assertFalse((self.root/extract.HEADER_NAME).exists())

    def test_exclusive_output_and_readonly_inputs(self):
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.root.glob('*.xml'))]
        output=self.root/extract.HEADER_NAME
        with patch.object(extract,'FINGERPRINTS',self.digests), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(extract.main(['--input-dir',str(self.root),'--output',str(output)]),0)
            content=output.read_bytes()
            self.assertEqual(extract.main(['--input-dir',str(self.root),'--output',str(output)]),1)
            self.assertEqual(output.read_bytes(),content)
        self.assertEqual(before,[hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.root.glob('*.xml'))])

    def test_symlink_output_refused(self):
        target=self.root/'target';target.write_text('SYNTHETIC unchanged')
        output=self.root/extract.HEADER_NAME;output.symlink_to(target)
        with patch.object(extract,'FINGERPRINTS',self.digests), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(extract.main(['--input-dir',str(self.root),'--output',str(output)]),1)
        self.assertEqual(target.read_text(),'SYNTHETIC unchanged')


if __name__=='__main__': unittest.main()
