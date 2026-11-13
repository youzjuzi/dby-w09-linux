# SPDX-License-Identifier: GPL-2.0-only
"""Offline structural contract for the single onboard speaker format rule."""
from pathlib import Path
import unittest
from spa_json import parse

ROOT = Path(__file__).resolve().parents[1]/'device-rootfs'
RULE = ROOT/'wireplumber/wireplumber.conf.d/60-dby-speaker-s16.conf'

class SpeakerFormat(unittest.TestCase):
    def test_exact_rule(self):
        self.assertEqual(parse('{'+RULE.read_text()+'}'), {
            'monitor.alsa.rules': [{'matches': [{'node.name':
                'alsa_output.platform-sound.HiFi__Speaker__sink'}],
                'actions': {'update-props': {'audio.format': 'S16LE'}}}]})
        self.assertFalse((ROOT/'etc/wireplumber').exists())

    def test_parser_rejects_duplicate_keys(self):
        with self.assertRaises(ValueError):
            parse('{ audio.format = "S16LE" audio.format = "S24LE" }')

if __name__ == '__main__':
    unittest.main()
