# SPDX-License-Identifier: GPL-2.0-only
"""Wizard label selection must not silently create duplicate networks."""
import unittest
from unittest.mock import patch
import test_seed_commands as F
from dbyinstall import seed_cli as C, seed_model as M


class WizardLabels(unittest.TestCase):
    def state(self, *labels):
        state = F.EMPTY
        for label in labels:
            state, _, _ = M.edit(state, {'wifi': label, 'ssid': F.__name__}, password=F.PASSPHRASE)
        return state

    def wizard(self, state, answers):
        values = iter(answers)
        prompts, messages = [], []
        def ask(prompt):
            prompts.append(prompt)
            return next(values, '')
        with patch.object(C, 'tty'):
            result = C.wizard(state, ask=ask, secret=lambda _: '', emit=messages.append)
        return result, prompts, messages

    def test_single_default_preserves_entire_state_without_duplicate(self):
        old = self.state('my-wifi')
        (changes, password, replacement, addition), prompts, messages = self.wizard(old, [''])
        self.assertEqual(changes['wifi'], 'my-wifi')
        self.assertIn('[my-wifi]', prompts[0])
        self.assertIn('my-wifi', messages)
        result, _, _ = M.edit(old, changes, password=password, replacement=replacement, addition=addition)
        self.assertTrue(result == old, 'wizard changed the full seed')
        preview = M.preview(old, result)
        self.assertEqual(preview['before'], preview['after'])
        for side in ('before', 'after'):
            self.assertEqual([s for s in preview[side] if M.WIFI.fullmatch(s)], ['wifi "my-wifi"'])
        self.assertEqual(len([s for s in M.parse(result['seed.conf'])[0] if M.WIFI.fullmatch(s)]), 1)

    def test_multiple_labels_require_nonempty_selection(self):
        (changes, *_), prompts, messages = self.wizard(self.state('alpha', 'beta'), ['', 'beta'])
        self.assertEqual(changes['wifi'], 'beta')
        self.assertEqual(prompts[0], prompts[1])
        self.assertNotIn('[', prompts[0])
        self.assertIn('alpha', messages); self.assertIn('beta', messages)
        self.assertTrue(any('no default' in m for m in messages))

    def test_no_network_defaults_main_and_requires_confirmation(self):
        (changes, *_), prompts, messages = self.wizard(F.EMPTY, ['', 'main'])
        self.assertEqual(changes['wifi'], 'main')
        self.assertIn('[main]', prompts[0])
        self.assertIn('No existing networks.', messages)
        self.assertIn('Repeat new', prompts[1])

    def test_new_label_rejected_then_return_to_existing(self):
        (changes, *_), prompts, messages = self.wizard(self.state('alpha'), ['new-label', '', ''])
        self.assertEqual(changes['wifi'], 'alpha')
        self.assertEqual(prompts[0], prompts[2])
        self.assertTrue(any('create a new network' in m for m in messages))

    def test_new_label_requires_exact_repeat(self):
        (changes, *_), prompts, _ = self.wizard(self.state('alpha', 'beta'), ['new-label', 'wrong', 'new-label', 'new-label'])
        self.assertEqual(changes['wifi'], 'new-label')
        self.assertEqual(prompts[1], prompts[3])
