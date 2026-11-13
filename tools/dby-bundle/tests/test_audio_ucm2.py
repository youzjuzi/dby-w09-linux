"""Separate playback/capture UCM contract; no hardware or mixer access."""
from pathlib import Path
import re
import unittest

UCM = Path(__file__).resolve().parents[1] / 'device-rootfs/ucm2/conf.d/sm8250'
# The validated analog route, with its frontend switch in SectionVerb.
MIC_ENABLE = (
    [(f'TX SMIC MUX{i}', v) for i, v in enumerate(('ADC0', 'ADC2', 'ADC3', 'ADC1'))]
    + [(f'TX DEC{i} MUX', 'SWR_MIC') for i in range(4)]
    + [(f'TX_AIF1_CAP Mixer DEC{i}', '1') for i in range(4)]
    + [(f'ADC{i} MUX', f'INP{i+1}') for i in range(2, 5)]
    + [(f'ADC{i}_MIXER Switch', '1') for i in range(1, 5)]
    + [(f'ADC{i} Volume', '9') for i in range(1, 5)]
    + [(f'ADC{i} Switch', '1') for i in range(1, 5)]
)
MIC_DISABLE = (
    [(f'ADC{i} Switch', '0') for i in range(4, 0, -1)]
    + [(f'ADC{i}_MIXER Switch', '0') for i in range(4, 0, -1)]
    + [('ADC4 MUX', 'INP5'), ('ADC3 MUX', 'INP4'), ('ADC2 MUX', 'INP2')]
    + [(f'TX_AIF1_CAP Mixer DEC{i}', '0') for i in range(3, -1, -1)]
    + [(f'TX DEC{i} MUX', 'MSM_DMIC') for i in range(3, -1, -1)]
    + [(f'TX SMIC MUX{i}', 'ZERO') for i in range(3, -1, -1)]
)


def sequence(text, name):
    block = re.search(name + r'\s*\[(.*?)\]', text, re.S).group(1)
    return re.findall(r'cset "name=\'(.*?)\' (.*?)"', block)


class AudioUCM(unittest.TestCase):
    def test_master_and_fallback(self):
        for name in ('DBY-W09.conf', 'sm8250.conf', 'HiFi.conf'):
            self.assertTrue((UCM/name).is_file())
            self.assertFalse((UCM/name).is_symlink())
        master = (UCM/'DBY-W09.conf').read_text()
        self.assertEqual(master, (UCM/'sm8250.conf').read_text())
        self.assertIn('Syntax 4', master)
        self.assertIn('File "/conf.d/sm8250/HiFi.conf"', master)

    def test_exact_capture_sequences(self):
        verb_speaker, mic = (UCM/'HiFi.conf').read_text().split('SectionDevice."Mic"')
        verb, speaker = verb_speaker.split('SectionDevice."Speaker"')
        self.assertEqual(sequence(verb, 'EnableSequence'),
                         [('DBY Speaker Safe Enable Switch', '0'),
                          ('DBY Speaker Attenuation Step', '9'),
                          ('DBY Speaker Input Atten 6dB Switch', '0'),
                          ('DBY Speaker Active Mask', '15'),
                          ('PRIMARY_TDM_RX_0 Audio Mixer MultiMedia1', '1'),
                          ('MultiMedia2 Mixer TX_CODEC_DMA_TX_3', '1')])
        self.assertEqual(sequence(verb, 'DisableSequence'),
                         [('DBY Speaker Safe Enable Switch', '0'),
                          ('PRIMARY_TDM_RX_0 Audio Mixer MultiMedia1', '0'),
                          ('MultiMedia2 Mixer TX_CODEC_DMA_TX_3', '0')])
        self.assertEqual(sequence(mic, 'EnableSequence'), MIC_ENABLE)
        self.assertEqual(sequence(mic, 'DisableSequence'), MIC_DISABLE)
        self.assertEqual(len(MIC_ENABLE) + 1, 28)
        self.assertNotRegex(str(MIC_ENABLE), r'RX_|WSA_|HPH|Spkr|EAR|AMIC2|INP2')
        self.assertNotIn('Playback', verb + mic)
        self.assertEqual(sequence(speaker, 'EnableSequence'),
                         [('DBY Speaker Attenuation Step', '9'),
                          ('DBY Speaker Active Mask', '15'),
                          ('DBY Speaker Safe Enable Switch', '1')])
        self.assertEqual(sequence(speaker, 'DisableSequence'),
                         [('DBY Speaker Safe Enable Switch', '0')])
        for control, value in sequence(verb, 'EnableSequence') + sequence(speaker, 'EnableSequence'):
            if control == 'DBY Speaker Attenuation Step':
                self.assertGreaterEqual(int(value), 9)
                self.assertLessEqual(int(value), 15)
        self.assertNotIn('MultiMedia1 Mixer TX_CODEC_DMA_TX_3', verb_speaker + mic)
        for value in ('PlaybackPCM "hw:${CardId},0"', 'PlaybackChannels 2',
                      'PlaybackPriority 200', 'Comment "Internal Speakers"'):
            self.assertIn(value, speaker)

    def test_capture_values(self):
        text = (UCM/'HiFi.conf').read_text()
        for value in ('CapturePCM "hw:${CardId},1"', 'CaptureChannels 4',
                      'CapturePriority 100', 'Comment "Internal Microphone"'):
            self.assertIn(value, text)


class AudioUCMParser(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/alsaucm').exists() and
                         Path('/usr/share/alsa/ucm2/ucm.conf').exists(),
                         'host alsaucm/UCM2 unavailable')
    def test_virtual_parser(self):
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('ucm.conf', 'lib'):
                (root/name).symlink_to(Path('/usr/share/alsa/ucm2')/name)
            shutil.copytree(UCM, root/'conf.d/sm8250')
            (root/'conf.virt.d').mkdir()
            (root/'conf.virt.d/DBY-W09.conf').symlink_to('../conf.d/sm8250/DBY-W09.conf')
            # Offline fixture only: no hardware CardId exists on a virtual card.
            # The shipped expression is asserted separately above, unchanged.
            hifi = root/'conf.d/sm8250/HiFi.conf'
            original = hifi.read_text()
            self.assertEqual(original.count('${CardId}'), 2)
            hifi.write_text(original.replace('${CardId}', '0'))
            result = subprocess.run(['/usr/bin/alsaucm', '-c', 'DBY-W09', 'dump', 'json'],
                                    env=os.environ | {'ALSA_CONFIG_UCM2': str(root)},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = json.loads(result.stdout)
            flat = json.dumps(parsed)
            for expected in ('HiFi', 'Speaker', 'Mic', 'CaptureChannels', 'PlaybackChannels', 'hw:0,0', 'hw:0,1'):
                self.assertIn(expected, flat)
            def values(obj, key):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == key:
                            yield v
                        yield from values(v, key)
                elif isinstance(obj, list):
                    for v in obj:
                        yield from values(v, key)
            self.assertIn('4', [str(v) for v in values(parsed, 'CaptureChannels')])

    @unittest.skipUnless(Path('/usr/bin/alsaucm').exists() and
                         Path('/usr/share/alsa/ucm2/ucm.conf').exists(),
                         'host alsaucm/UCM2 unavailable')
    def test_verb_and_device_share_manager(self):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('ucm.conf', 'lib'):
                (root/name).symlink_to(Path('/usr/share/alsa/ucm2')/name)
            (root/'conf.virt.d').mkdir()
            (root/'conf.virt.d/SessionProbe.conf').write_text(
                'Syntax 4\nSectionUseCase."HiFi" { File "HiFi.conf" Comment "test" }\n')
            (root/'conf.virt.d/HiFi.conf').write_text(
                'SectionVerb { Value { TQ "HiFi" } }\n'
                'SectionDevice."Mic" { Comment "test" Value { CapturePCM "null" } }\n')
            # No cset, PCM open, or real sound card: exercise manager lifetime only.
            def run(args):
                return subprocess.run(['/usr/bin/alsaucm', '-c', 'SessionProbe'] + args,
                    env=os.environ | {'ALSA_CONFIG_UCM2': str(root)},
                    capture_output=True, text=True, timeout=10)
            self.assertEqual(run(['set', '_verb', 'HiFi']).returncode, 0)
            self.assertNotEqual(run(['set', '_enadev', 'Mic']).returncode, 0)
            self.assertEqual(run(['set', '_verb', 'HiFi', 'set', '_enadev', 'Mic']).returncode, 0)


if __name__ == '__main__':
    unittest.main()
