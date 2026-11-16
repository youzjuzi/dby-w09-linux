# SPDX-License-Identifier: GPL-2.0-only
"""Power-key policy, restoration and package integration without a live bus/input."""
import ast
import configparser
import importlib.machinery
import importlib.util
import os
import json
import shutil
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TOOL = Path(__file__).resolve().parents[1]
SOURCE = TOOL / 'device-rootfs/bin/dby-w09-powerkey'
loader = importlib.machinery.SourceFileLoader('powerkey', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
P = importlib.util.module_from_spec(spec)
loader.exec_module(P)
sys.path.insert(0, str(TOOL))
from dbybundle.build_archive import overlay


class FakeDesktop:
    def __init__(self, mode=0, action='suspend'):
        self.current_mode, self.action = mode, action
        self.calls = []
        self.success = True
        self.dialog = None
        self.lid_actions = dict.fromkeys(P.LID_SETTINGS, "suspend")
        self.transactions = []

    def mode(self):
        self.calls.append(('get',))
        return self.current_mode

    def set_mode(self, mode):
        self.calls.append(('set', mode))
        if self.success:
            self.current_mode = mode
        return self.success

    def shutdown_dialog(self):
        self.calls.append(('dialog',))
        return 'requested' if self.success else 'failed'

    def poll_dialog(self):
        pass

    def close_dialog(self):
        pass

    def get_action(self):
        return self.action

    def set_action(self, action):
        self.calls.append(('action', action))
        if self.success:
            self.action = action
        return self.success

    def get_actions(self):
        return {P.SETTING: self.get_action(), **self.lid_actions}

    def set_actions(self, values):
        self.transactions.append(values.copy())
        ok = self.set_action(values[P.SETTING])
        if ok:
            self.lid_actions = {key: values[key] for key in P.LID_SETTINGS}
        return ok


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.bus = FakeDesktop()
        self.logs = []
        self.key = P.PowerKey(self.bus, clock=lambda: self.now, emit=self.logs.append,
                              screen=lambda: self.bus.current_mode == 0)

    def key_event(self, value, at):
        self.now = at
        self.key.event(1, 116, value)

    def test_short_release_only(self):
        self.key_event(1, 0)
        self.assertEqual(self.bus.calls, [])
        self.key_event(0, .1)
        self.assertEqual(self.bus.calls, [('set', 1)])
        self.assertIn('100 ms', self.logs[0])

    def test_long_fires_at_800_while_held_once(self):
        self.now = .2
        self.key.event(1, 116, 1, 0)
        self.assertAlmostEqual(self.key.timeout(), .6)
        self.now = .799
        self.key.tick()
        self.assertEqual(self.bus.calls, [])
        self.now = .8
        self.key.tick()
        self.assertEqual(self.bus.calls, [('dialog',)])
        self.assertEqual(self.logs, ['long 800 ms requested lag=200 ms'])
        self.assertIsNotNone(self.key.pressed)
        self.now = 2
        self.key.tick()
        self.key_event(0, 3)
        self.assertEqual(self.bus.calls.count(('dialog',)), 1)
        self.assertFalse(any(c[0] == 'set' for c in self.bus.calls))

    def test_every_keydown_decision_table_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connector = root / 'drm/card7-DSI-1'
            connector.mkdir(parents=True)
            backlight = root / 'backlight/rt8555-backlight'
            backlight.mkdir(parents=True)
            for enabled, dpms, power, state in [
                ('enabled', 'On', '0', True), ('disabled', 'Off', '4', False),
                ('enabled', 'Off', '0', False), ('disabled', 'On', '0', False),
                ('enabled', 'On', '4', False), ('enabled', 'Standby', '1', False),
                ('enabled', 'Suspend', '2', False), ('enabled', 'On', '3', False),
                ('unknown', 'On', '0', None), ('enabled', 'unknown', '0', None),
                ('enabled', 'On', '5', None), ('enabled', 'On', None, None),
            ]:
                with self.subTest(enabled=enabled, dpms=dpms, power=power):
                    (connector / 'enabled').write_text(enabled + '\n')
                    (connector / 'dpms').write_text(dpms + '\n')
                    if power is None:
                        (backlight / 'bl_power').unlink()
                    else:
                        (backlight / 'bl_power').write_text(power + '\n')
                    self.assertIs(P.screen_state(root), state)
                    self.now = 0
                    bus = FakeDesktop(3)  # GNOME idle mode must not decide the gesture.
                    logs = []
                    key = P.PowerKey(bus, lambda: self.now, logs.append,
                                     screen=lambda: P.screen_state(root))
                    key.event(1, 116, 1, 0)
                    self.assertEqual(key.woke, state is False)
                    self.now = .1
                    key.event(1, 116, 0, .1)
                    expected = ([('set', 0)] if state is False else
                                [('set', 1)] if state is True else [])
                    self.assertEqual(bus.calls, expected)
                    if state is None:
                        self.assertEqual(logs, ['screen state unavailable'])
                    else:
                        self.assertIn('lag=0 ms', logs[0])
            (backlight / 'bl_power').write_text('0\n')
            (root / 'drm/card8-DSI-1').mkdir()
            self.assertIsNone(P.screen_state(root))
            (root / 'drm/card8-DSI-1').rmdir()
            (connector / 'dpms').unlink()
            self.assertIsNone(P.screen_state(root))
            (connector / 'enabled').unlink()
            connector.rmdir()
            self.assertIsNone(P.screen_state(root))

    def test_wake_hold_and_release_do_not_blank_or_open_dialog(self):
        self.bus.current_mode = 1
        self.key_event(1, 0)
        self.now = 1
        self.key.tick()
        self.key_event(0, 2)
        self.assertEqual(self.bus.calls, [('set', 0)])

    def test_repeat_other_keys_and_duplicate_down_ignored(self):
        self.key.event(1, 115, 1)
        self.key.event(0, 116, 1)
        self.key_event(0, 0)
        self.key_event(2, 0)
        self.assertEqual(self.bus.calls, [])
        self.key_event(1, 0)
        self.key_event(1, .1)
        self.key_event(2, .2)
        self.key_event(0, .3)
        self.assertEqual(self.bus.calls, [('set', 1)])

    def test_failed_actions_are_not_repeated_and_state_is_honest(self):
        self.bus.success = False
        self.key_event(1, 0)
        self.key_event(0, .1)
        self.key_event(1, 0)
        self.now = .8
        self.key.tick(); self.key.tick()
        self.key_event(0, 1)
        self.assertEqual(self.bus.calls.count(('dialog',)), 1)
        self.assertTrue(all('failed' in line for line in self.logs))

    def test_release_delayed_past_threshold_is_not_a_short_or_late_dialog(self):
        # Both events can be dequeued together, long after the physical press.
        for release, expected in [(10.125, [('set', 1)]), (10.8, []), (11, [])]:
            with self.subTest(release=release):
                self.bus.calls.clear()
                self.bus.current_mode = 0
                self.now = 20
                self.key.event(1, 116, 1, 10)
                self.assertEqual(self.key.timeout(), 0)
                self.key.event(1, 116, 0, release)
                self.key.tick()
                self.assertEqual(self.bus.calls, expected)
        self.assertEqual(self.logs, ['short 125 ms ok lag=9875 ms'])

    def test_timer_timeout(self):
        self.assertIsNone(self.key.timeout())
        self.key_event(1, 0)
        self.assertEqual(self.key.timeout(), .8)
        self.now = .75
        self.assertAlmostEqual(self.key.timeout(), .05)
        self.now = 1
        self.assertEqual(self.key.timeout(), 0)

    def test_no_timer_for_wake_suppressed_or_fired_gesture(self):
        for state in ('woke', 'suppressed', 'long_fired'):
            with self.subTest(state=state):
                self.key = P.PowerKey(self.bus, lambda: self.now)
                self.key.pressed = 0
                setattr(self.key, state, True)
                self.assertIsNone(self.key.timeout())


class DesktopTests(unittest.TestCase):
    def test_property_parser_and_variant_single_argument(self):
        for text, wanted in [('(<0>,)', 0), ('(<int32 1>,)', 1), ('(<-1>,)', -1), ('bad', None)]:
            runner = Mock(return_value=Mock(returncode=0, stdout=text))
            bus = P.Desktop(runner, lambda _: None)
            self.assertEqual(bus.mode(), wanted)
        runner = Mock(return_value=Mock(returncode=0, stdout='()'))
        child = Mock()
        child.poll.return_value = None
        popen = Mock(return_value=child)
        bus = P.Desktop(runner, popen=popen)
        self.assertTrue(bus.set_mode(1))
        self.assertEqual(runner.call_args.args[0][-1], '<int32 1>')
        self.assertEqual(runner.call_args.kwargs['timeout'], 5)
        self.assertEqual(bus.shutdown_dialog(), 'requested')
        self.assertEqual(popen.call_args.args[0][-1], 'org.gnome.SessionManager.Shutdown')
        child.wait.assert_not_called()

    def test_bus_failures_timeout_and_missing_commands_do_not_escape(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired('gdbus', 5),
                        subprocess.SubprocessError()):
            bus = P.Desktop(Mock(side_effect=failure), lambda _: None)
            self.assertIsNone(bus.mode())
            self.assertFalse(bus.set_mode(0))
            self.assertIsNone(bus.get_action())
            self.assertFalse(bus.set_action('suspend'))
        logs = []
        bus = P.Desktop(Mock(return_value=Mock(returncode=1, stdout='private', stderr='private')), logs.append)
        self.assertFalse(bus.set_mode(1))
        self.assertNotIn('private', str(logs))


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.logs = []
        self.child = Mock()
        self.child.poll.return_value = None
        self.popen = Mock(return_value=self.child)
        self.runner = Mock(return_value=Mock(returncode=0, stdout='(<0>,)'))
        self.bus = P.Desktop(self.runner, self.logs.append, self.popen, lambda: self.now)
        self.key = P.PowerKey(self.bus, lambda: self.now, self.logs.append, screen=lambda: True)

    def hold(self, start):
        self.now = start
        self.key.event(1, 116, 1)
        self.now = start + 0.801
        self.key.tick()

    def test_long_starts_once_without_waiting_and_discards_output(self):
        self.child.wait.side_effect = AssertionError('tick must never wait')
        self.key.event(1, 116, 1)
        self.now = .8
        self.key.tick(); self.key.tick()
        self.key.event(1, 116, 0)
        self.popen.assert_called_once()
        self.child.wait.assert_not_called()
        self.assertEqual(self.runner.call_count, 0)  # No key-down Get.
        self.assertEqual(self.logs, ['long 800 ms requested lag=?'])
        self.assertEqual(self.popen.call_args.kwargs, {
            'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL})

    def test_pending_second_long_ignored_but_short_press_still_works(self):
        self.hold(0)
        self.key.event(1, 116, 0)
        self.hold(2)
        self.assertEqual(self.logs[-1], 'long 801 ms ignored (dialog pending) lag=?')
        self.popen.assert_called_once()
        self.key.event(1, 116, 0)
        self.now = 4
        self.key.event(1, 116, 1)
        self.now = 4.1
        self.key.event(1, 116, 0)
        self.assertIn('short 100 ms ok lag=?', self.logs)
        self.child.wait.assert_not_called()

    def test_nonzero_exit_is_closed_not_failed_and_allows_next_request(self):
        self.hold(0)
        self.key.event(1, 116, 0)
        self.child.poll.return_value = 1
        self.now = 2.801
        self.bus.poll_dialog()
        self.assertEqual(self.logs[-1], 'dialog closed rc=1 after 2000 ms')
        self.assertIsNone(self.bus.dialog)
        self.assertFalse(any('failed' in line for line in self.logs))
        self.child.poll.return_value = None
        self.hold(4)
        self.assertEqual(self.popen.call_count, 2)

    def test_spawn_oserror_logs_failed_and_next_gesture_can_retry(self):
        self.popen.side_effect = [OSError('private diagnostic'), self.child]
        self.hold(0)
        self.key.tick()
        self.assertEqual(self.logs, ['long 801 ms failed lag=?'])
        self.assertIsNone(self.bus.dialog)
        self.key.event(1, 116, 0)
        self.hold(2)
        self.assertEqual(self.logs[-1], 'long 801 ms requested lag=?')
        self.assertEqual(self.popen.call_count, 2)

    def test_long_fallback_is_nonblocking_and_escalates_only_after_grace(self):
        self.assertEqual(self.bus.shutdown_dialog(), 'requested')
        for now in (5, 60, 119.999):
            self.now = now; self.bus.poll_dialog()
        self.child.terminate.assert_not_called()
        self.now = 120
        self.bus.poll_dialog(); self.bus.poll_dialog()
        self.child.terminate.assert_called_once()
        self.child.wait.assert_not_called()
        self.now = 121.999
        self.bus.poll_dialog()
        self.child.kill.assert_not_called()
        self.now = 122
        self.bus.poll_dialog(); self.bus.poll_dialog()
        self.child.kill.assert_called_once()
        self.child.poll.return_value = -9
        self.now = 123
        self.bus.poll_dialog()
        self.assertEqual(self.logs[-1], 'dialog closed rc=-9 after 123000 ms')

    def test_close_terminates_and_waits_bounded_then_reaps(self):
        self.bus.shutdown_dialog()
        def wait(timeout):
            self.assertEqual(timeout, 2)
            self.child.poll.return_value = -15
        self.child.wait.side_effect = wait
        self.bus.close_dialog()
        self.child.terminate.assert_called_once()
        self.child.wait.assert_called_once_with(timeout=2)
        self.child.kill.assert_not_called()
        self.assertIsNone(self.bus.dialog)

    def test_close_kills_if_terminate_does_not_finish(self):
        self.bus.shutdown_dialog()
        def wait(timeout):
            if self.child.wait.call_count == 1:
                raise subprocess.TimeoutExpired('gdbus', timeout)
            self.child.poll.return_value = -9
        self.child.wait.side_effect = wait
        self.bus.close_dialog()
        self.child.terminate.assert_called_once()
        self.child.kill.assert_called_once()
        self.assertEqual(self.child.wait.call_count, 2)
        self.assertIsNone(self.bus.dialog)

    def test_main_cleans_child_before_restoring_on_normal_and_signal_exit(self):
        for sig in (None, signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as tmp:
                self.setUp()
                order = []
                self.bus.get_actions = lambda: dict.fromkeys(P.SETTINGS, 'interactive')
                self.bus.set_actions = lambda values: order.append(values[P.SETTING]) or True
                self.child.terminate.side_effect = lambda: order.append('terminate')
                def wait(timeout):
                    order.append('wait')
                    self.child.poll.return_value = -15
                self.child.wait.side_effect = wait
                fake_input = Path(tmp) / 'input'; fake_input.touch()
                def loop(fd, settings, desktop, stop):
                    self.assertTrue(settings.take())
                    self.assertEqual(desktop.shutdown_dialog(), 'requested')
                    if sig:
                        signal.getsignal(sig)(sig, None)
                        self.assertTrue(stop())
                with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
                    self.assertEqual(P.main([], self.bus, lambda: fake_input, loop), 0)
                self.assertEqual(order, ['nothing', 'terminate', 'wait', 'interactive'])

    def test_event_loop_polls_on_every_cycle_without_waiting(self):
        self.bus.shutdown_dialog()
        cycles = 0
        def select_once(*args):
            nonlocal cycles
            cycles += 1
            return [], [], []
        with patch.object(P.select, 'select', side_effect=select_once):
            P.event_loop(0, Mock(take=Mock(return_value=True)), self.bus, lambda: cycles == 3)
        self.assertEqual(self.child.poll.call_count, 3)
        self.child.wait.assert_not_called()


class IdleWaitTests(unittest.TestCase):
    def run_steps(self, bus, steps, screen=True, event_clock=True):
        now = [0]
        seen = []
        input_fd = 123456  # Never used by the real select/read APIs.
        def select_step(readers, writers, errors, timeout):
            self.assertEqual(len(readers), 2)
            self.assertEqual(readers[0], input_fd)
            at, event = steps[len(seen)]
            seen.append(timeout)
            now[0] = at
            if event is not None:
                self.data = P.EVENT.pack(int(at), round((at % 1) * 1_000_000), *event)
                return [input_fd], [], []
            return [], [], []
        with patch.object(P.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(P.select, 'select', side_effect=select_step), \
                patch.object(P.os, 'read', side_effect=lambda fd, size: self.data), \
                patch.object(P, 'log'), patch.object(P, 'screen_state', return_value=screen), \
                patch.object(P.fcntl, 'ioctl', side_effect=None if event_clock else OSError) as ioctl:
            P.event_loop(input_fd, Mock(take=Mock(return_value=True)), bus,
                         lambda: len(seen) == len(steps))
            ioctl.assert_called_once_with(input_fd, 0x400445a0, P.struct.pack('i', P.time.CLOCK_MONOTONIC))
        return seen

    def test_active_idle_on_or_off_has_no_periodic_timeout(self):
        for mode in (0, 1, 3, -1):
            with self.subTest(mode=mode):
                bus = FakeDesktop(mode)
                self.assertEqual(self.run_steps(bus, [(10, None)]), [None])
                self.assertEqual(bus.calls, [('get',)])

    def test_select_gets_exact_remaining_hold_time_then_blocks(self):
        waits = self.run_steps(FakeDesktop(), [
            (0, (1, 116, 1)), (.3, (1, 115, 1)), (.8, None), (2, None)])
        self.assertIsNone(waits[0])
        self.assertAlmostEqual(waits[1], .8)
        self.assertAlmostEqual(waits[2], .5)
        self.assertIsNone(waits[3])

    def test_short_release_cancels_timer_and_waits_indefinitely(self):
        for event_clock in (True, False):
            bus = FakeDesktop()
            waits = self.run_steps(bus, [(0, (1, 116, 1)), (.1, (1, 116, 0)), (3, None)],
                                   event_clock=event_clock)
            self.assertEqual(waits, [None, .8, None])
            self.assertIn(('set', 1), bus.calls)

    def test_wake_and_suppressed_gestures_have_no_long_timer(self):
        for screen in (False, None):
            with self.subTest(screen=screen):
                bus = FakeDesktop()
                waits = self.run_steps(bus, [(0, (1, 116, 1)), (1, None)], screen=screen)
                self.assertEqual(waits, [None, None])
                self.assertNotIn(('dialog',), bus.calls)

    def test_unready_retry_uses_remaining_two_seconds_despite_input(self):
        bus = FakeDesktop(None)
        waits = self.run_steps(bus, [(.5, (1, 115, 1)), (2, None), (4, None)])
        self.assertEqual(waits, [2, 1.5, 2])
        self.assertEqual(bus.calls, [('get',), ('get',)])

    def test_retry_success_switches_to_idle_blocking(self):
        bus = FakeDesktop()
        bus.mode = Mock(side_effect=[None, 0])
        self.assertEqual(self.run_steps(bus, [(2, None), (5, None)]), [2, None])

    def test_dialog_polling_only_while_pending_and_hold_deadline_wins(self):
        bus = FakeDesktop()
        bus.dialog = object()
        bus.poll_dialog = Mock(side_effect=lambda: setattr(bus, 'dialog', None)
                               if bus.poll_dialog.call_count == 4 else None)
        waits = self.run_steps(bus, [(0, (1, 116, 1)), (.8, None), (2, None), (4, None)])
        self.assertEqual(waits, [1, .8, 1, None])
        self.assertEqual(bus.poll_dialog.call_count, 4)

    def test_real_signal_wakes_idle_select_and_restores_resources(self):
        # Real process-local signals and pipes, fake input and desktop only.
        # The signal lands in the check-to-select window: it must not be lost.
        actual_select = P.select.select
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as tmp:
                input_read, input_write = os.pipe()
                old_read, old_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                previous_fd = signal.set_wakeup_fd(old_write)
                handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
                stream = os.fdopen(input_read, 'rb', buffering=0)
                finder = Mock(return_value=Mock(open=Mock(return_value=stream)))
                bus = FakeDesktop(action='interactive')
                captured = []
                def interrupt_wait(readers, writers, errors, timeout):
                    self.assertIsNone(timeout)
                    captured.extend(readers[1:])
                    os.kill(os.getpid(), sig)
                    ready = actual_select(readers, writers, errors, 1)
                    self.assertIn(readers[1], ready[0], 'signal did not wake the pipe')
                    return ready
                try:
                    with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp), \
                            patch.object(P.select, 'select', side_effect=interrupt_wait):
                        self.assertEqual(P.main([], bus, finder), 0)
                    self.assertEqual(bus.action, 'interactive')
                    self.assertEqual(bus.calls, [('get',), ('action', 'nothing'), ('action', 'interactive')])
                    self.assertEqual(signal.set_wakeup_fd(old_write), old_write)
                    for s, handler in handlers.items():
                        self.assertEqual(signal.getsignal(s), handler)
                    self.assertEqual(len(captured), 1)
                    with self.assertRaises(OSError):
                        os.fstat(captured[0])
                finally:
                    signal.set_wakeup_fd(previous_fd)
                    stream.close()
                    for fd in (input_write, old_read, old_write):
                        os.close(fd)

    def test_signal_pipe_restored_and_both_ends_closed_on_loop_error(self):
        pipe2 = os.pipe2
        created = []
        def make_pipe(flags):
            pair = pipe2(flags)
            created.extend(pair)
            return pair
        previous = signal.set_wakeup_fd(-1)
        signal.set_wakeup_fd(previous)
        with patch.object(P.os, 'pipe2', side_effect=make_pipe), \
                patch.object(P.select, 'select', side_effect=OSError('test input error')):
            with self.assertRaises(OSError):
                P.event_loop(123456, Mock(take=Mock(return_value=True)), FakeDesktop(), lambda: False)
        restored = signal.set_wakeup_fd(previous)
        self.assertEqual(restored, previous)
        self.assertEqual(len(created), 2)
        for fd in created:
            with self.assertRaises(OSError):
                os.fstat(fd)


class SettingsTests(unittest.TestCase):
    def test_root_start_and_restore_skip_before_any_lookup_or_action(self):
        with patch.object(P.os, 'getuid', return_value=0), \
                patch.object(P.pwd, 'getpwuid') as lookup, patch.object(P, 'log') as log:
            bus = FakeDesktop()
            finder = Mock()
            loop = Mock()
            for args in ([], ['--restore']):
                self.assertEqual(P.main(args, bus, finder, loop), 0)
            lookup.assert_not_called()
            finder.assert_not_called()
            loop.assert_not_called()
            log.assert_not_called()
            self.assertEqual(bus.calls, [])

    def test_greeter_start_and_stop_post_do_not_change_settings(self):
        for account in ('gdm', 'Debian-gdm'):
            with patch.object(P.pwd, 'getpwuid', return_value=Mock(pw_name=account)):
                bus = FakeDesktop()
                finder = Mock()
                self.assertEqual(P.main([], desktop=bus, finder=finder), 0)
                self.assertEqual(P.main(['--restore'], desktop=bus, finder=finder), 0)
                self.assertEqual(bus.calls, [])
                finder.assert_not_called()

    def test_save_permissions_restore_twice_all_original_values(self):
        for original in P.ACTIONS:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                bus = FakeDesktop(action=original)
                settings = P.Settings(bus, tmp)
                # A stale previous run must not override the current user preference.
                settings.path.write_text('hibernate\n')
                self.assertTrue(settings.take())
                self.assertTrue(settings.taken)
                self.assertEqual(json.loads(settings.path.read_text()), {P.SETTING: original, **dict.fromkeys(P.LID_SETTINGS, 'suspend')})
                self.assertEqual(stat.S_IMODE(settings.path.stat().st_mode), 0o600)
                self.assertEqual(bus.action, 'nothing')
                self.assertTrue(settings.restore())
                self.assertEqual(bus.action, original)
                self.assertTrue(P.Settings(bus, tmp).restore())
                self.assertEqual(bus.action, original)

    def test_missing_invalid_or_symlink_saved_file_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = FakeDesktop(action='nothing')
            settings = P.Settings(bus, tmp)
            def check_noop():
                self.assertTrue(settings.restore())
                with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
                    self.assertEqual(P.main(['--restore'], desktop=bus), 0)
                self.assertEqual(bus.action, 'nothing')
                self.assertEqual(bus.calls, [])
            check_noop()
            settings.path.write_text('not-a-setting')
            check_noop()
            other = Path(tmp) / 'other'; other.write_text('interactive')
            settings.path.unlink(); settings.path.symlink_to(other)
            check_noop()
            self.assertEqual(other.read_text(), 'interactive')

    def test_failed_set_retry_keeps_saved_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = FakeDesktop(action='interactive')
            bus.success = False
            settings = P.Settings(bus, tmp)
            self.assertFalse(settings.take())
            self.assertFalse(settings.taken)
            bus.action = 'nothing'  # Simulate a timeout after the setting was applied.
            bus.success = True
            self.assertTrue(settings.take())
            self.assertTrue(settings.taken)
            self.assertEqual(json.loads(settings.path.read_text())[P.SETTING], 'interactive')
            bus.success = False
            self.assertFalse(settings.take())
            self.assertTrue(settings.taken)  # A later failure cannot erase prior success.
            bus.success = True
            # ExecStopPost path after simulated loss of the daemon's memory.
            self.assertTrue(P.Settings(bus, tmp).restore())
            self.assertEqual(bus.action, 'interactive')

    def test_failed_takeover_does_not_restore_in_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_input = Path(tmp) / 'input'; fake_input.touch()
            bus = FakeDesktop(action='interactive')
            bus.success = False
            def loop(fd, settings, desktop, stop):
                self.assertFalse(settings.take())
                self.assertFalse(settings.taken)
                self.assertTrue(settings.path.exists())
            with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
                self.assertEqual(P.main([], desktop=bus, finder=lambda: fake_input, loop=loop), 0)
                self.assertEqual(bus.calls, [('action', 'nothing')])
                # A failed call could have applied before timing out: a valid
                # pre-takeover file still authorizes the independent StopPost.
                bus.success = True
                self.assertEqual(P.main(['--restore'], desktop=bus), 0)
                self.assertEqual(bus.calls[-1], ('action', 'interactive'))

    def test_input_open_permission_failure_does_not_restore(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
            fake_input = Mock()
            fake_input.open.side_effect = PermissionError
            bus = FakeDesktop(action='interactive')
            self.assertEqual(P.main([], desktop=bus, finder=lambda: fake_input), 1)
            fake_input.open.assert_called_once_with('rb', buffering=0)
            self.assertEqual(bus.calls, [])
            self.assertEqual(bus.action, 'interactive')

    def test_input_error_after_takeover_restores(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
            fake_input = Path(tmp) / 'input'; fake_input.touch()
            bus = FakeDesktop(action='interactive')
            def loop(fd, settings, desktop, stop):
                self.assertTrue(settings.take())
                raise OSError('input disconnected')
            self.assertEqual(P.main([], desktop=bus, finder=lambda: fake_input, loop=loop), 1)
            self.assertEqual(bus.calls, [('action', 'nothing'), ('action', 'interactive')])
            self.assertEqual(bus.action, 'interactive')

    def test_failed_read_or_save_never_takes_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = FakeDesktop(action=None)
            settings = P.Settings(bus, tmp, lambda _: None)
            self.assertFalse(settings.take())
            self.assertFalse(settings.taken)
            self.assertFalse(settings.path.exists())
            self.assertEqual(bus.calls, [])
            bus.action = 'suspend'
            with patch.object(P.tempfile, 'mkstemp', side_effect=OSError):
                self.assertFalse(settings.take())
            self.assertEqual(bus.calls, [])

    def test_normal_and_signal_exit_restore(self):
        for sig in (None, signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as tmp:
                fake_input = Path(tmp) / 'input'; fake_input.touch()
                bus = FakeDesktop(action='interactive')
                def loop(fd, settings, desktop, stop):
                    self.assertTrue(settings.take())
                    if sig:
                        signal.getsignal(sig)(sig, None)
                        self.assertTrue(stop())
                with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
                    self.assertEqual(P.main([], desktop=bus, finder=lambda: fake_input, loop=loop), 0)
                    self.assertEqual(bus.action, 'interactive')
                    self.assertEqual(P.main(['--restore'], desktop=bus), 0)
                    self.assertEqual(bus.action, 'interactive')

    def test_missing_device_nonzero_and_exact_name_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for event, name in [('event2', 'pm8941_pwrkey extra'), ('event7', 'pm8941_pwrkey')]:
                path = root / event / 'device'; path.mkdir(parents=True)
                (path / 'name').write_text(name + '\n')
            self.assertEqual(P.find_device(root, root / 'dev'), root / 'dev/event7')
            bus = FakeDesktop(action='interactive')
            with patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
                code = P.main([], desktop=bus, finder=Mock(side_effect=FileNotFoundError))
            self.assertEqual(code, 1)
            self.assertEqual(bus.calls, [])
            self.assertEqual(bus.action, 'interactive')


class LidTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeDesktop()
        self.key = P.PowerKey(self.bus, clock=lambda: 4, emit=lambda _: None,
                              screen=lambda: self.bus.current_mode == 0)
        self.logs = []
        self.lid = P.Lid(self.key, self.logs.append)

    def test_close_open_duplicates_and_invalid_values(self):
        for value in (1, 1, 2, -1, 0, 0):
            self.lid.state(value)
        self.assertEqual(self.bus.calls, [('set', 1), ('set', 0)])
        self.assertEqual(self.logs, ['lid closed ok', 'lid open ok'])

    def test_start_closed_blanks_but_start_open_does_not_wake(self):
        self.lid.state(0, initial=True)
        self.assertEqual(self.bus.calls, [])
        self.lid.last = None
        self.lid.state(1, initial=True)
        self.assertEqual(self.bus.calls, [('set', 1)])

    def test_closed_lid_does_not_reblank_after_power_key_wakes(self):
        self.lid.state(1)
        self.key.event(1, 116, 1)
        self.key.event(1, 116, 0)
        self.lid.state(1)
        self.assertEqual(self.bus.calls, [('set', 1), ('set', 0)])
        self.lid.state(0)
        self.assertEqual(self.bus.calls[-1], ('set', 0))

    def test_failure_logged_without_repeating_same_state(self):
        self.bus.success = False
        self.lid.state(1); self.lid.state(1)
        self.assertEqual(self.bus.calls, [('set', 1)])
        self.assertEqual(self.logs, ['lid closed failed'])
        self.bus.success = True
        self.lid.state(0)
        self.assertEqual(self.logs[-1], 'lid open ok')

    def test_only_lid_switch_events_and_partial_records(self):
        data = b''.join(P.EVENT.pack(0, 0, *e) for e in [(5, 10, 1), (1, 116, 1), (5, 0, 1), (5, 0, 0)])
        with patch.object(P.os, 'read', side_effect=[data[:17], data[17:]]):
            self.lid.read(9, lambda: False)
            self.assertEqual(self.bus.calls, [])
            self.lid.read(9, lambda: False)
        self.assertEqual(self.bus.calls, [('set', 1), ('set', 0)])

    def test_overflow_discards_until_report_then_resynchronizes(self):
        data = b''.join(P.EVENT.pack(0, 0, *e) for e in [(0, 3, 0), (5, 0, 1), (0, 0, 0)])
        with patch.object(P.os, 'read', return_value=data), patch.object(P, 'lid_state', return_value=0) as state:
            self.lid.read(9, lambda: False)
        state.assert_called_once_with(9)
        self.assertEqual(self.bus.calls, [('set', 0)])

    def test_ioctl_is_readonly_single_switch_bit(self):
        def read_bits(fd, request, bits, mutate):
            self.assertEqual((fd, request, len(bits), mutate), (9, 0x8001451b, 1, True))
            bits[0] = 0b10000001
        with patch.object(P.fcntl, 'ioctl', side_effect=read_bits):
            self.assertEqual(P.lid_state(9), 1)

    def test_discovery_exact_name_and_missing_logs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for event, name in [('event2', 'hall-probe'), ('event3', 'hall-lid extra'), ('event9', 'hall-lid')]:
                entry = root / event / 'device'; entry.mkdir(parents=True)
                (entry / 'name').write_text(name)
            self.assertEqual(P.find_lid(root, root / 'dev'), root / 'dev/event9')
        with patch.object(P, 'find_lid', side_effect=FileNotFoundError), patch.object(P, 'log') as log:
            with P.optional_lid() as fd:
                self.assertIsNone(fd)
        log.assert_called_once_with('lid input unavailable; power key only')

    def test_lid_descriptor_setup_failure_falls_back_and_closes(self):
        stream = Mock()
        with patch.object(P, 'find_lid', return_value=Mock(open=Mock(return_value=stream))), \
                patch.object(P.os, 'set_blocking', side_effect=OSError), patch.object(P, 'log') as log:
            with P.optional_lid() as fd:
                self.assertIsNone(fd)
        stream.close.assert_called_once()
        log.assert_called_once()

    def test_event_loop_sync_only_after_takeover_and_lid_failure_keeps_power(self):
        calls = []
        settings = Mock()
        settings.take.side_effect = [False, True]
        stopped = [False]
        clock = [0]
        turns = [0]
        def select_step(readers, w, x, timeout):
            turns[0] += 1
            calls.append((list(readers), timeout))
            if turns[0] == 1:
                self.assertEqual(self.bus.calls, [('get',)])
                clock[0] = 2
                return [], [], []
            if turns[0] == 2:
                return [12], [], []  # Disconnect only the lid.
            stopped[0] = True
            return [], [], []
        with patch.object(P.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(P.select, 'select', side_effect=select_step), \
                patch.object(P, 'lid_state', return_value=1) as state, \
                patch.object(P.os, 'read', return_value=b''), patch.object(P, 'log'):
            P.read_events(10, 11, settings, self.bus, lambda: stopped[0], 12)
        state.assert_called_once_with(12)
        self.assertEqual(calls, [([10, 11], 2), ([10, 11, 12], None), ([10, 11], None)])
        self.assertIn(('set', 1), self.bus.calls)

    def test_logind_dropin_exact_keys(self):
        cfg = configparser.ConfigParser(interpolation=None); cfg.optionxform = str
        cfg.read(TOOL / 'device-rootfs/etc/systemd/logind.conf.d/10-dby-w09-lid.conf')
        self.assertEqual(cfg.sections(), ['Login'])
        self.assertEqual(dict(cfg['Login']), dict.fromkeys(
            ['HandleLidSwitch', 'HandleLidSwitchExternalPower', 'HandleLidSwitchDocked'], 'ignore'))

    def test_powerkey_logind_dropin_exact_keys(self):
        cfg = configparser.ConfigParser(interpolation=None); cfg.optionxform = str
        cfg.read(TOOL / 'device-rootfs/etc/systemd/logind.conf.d/20-dby-w09-powerkey.conf')
        self.assertEqual(cfg.sections(), ['Login'])
        self.assertEqual(dict(cfg['Login']), {'HandlePowerKey': 'ignore',
                                            'HandlePowerKeyLongPress': 'poweroff'})


class MultiSettingsTests(unittest.TestCase):
    def test_all_values_saved_0600_and_restored_in_one_call(self):
        bus = FakeDesktop(action='interactive')
        bus.lid_actions = dict(zip(P.LID_SETTINGS, ['blank', 'hibernate']))
        original = bus.get_actions()
        with tempfile.TemporaryDirectory() as tmp:
            settings = P.Settings(bus, tmp)
            self.assertTrue(settings.take())
            self.assertEqual(json.loads(settings.path.read_text()), original)
            self.assertEqual(stat.S_IMODE(settings.path.stat().st_mode), 0o600)
            self.assertEqual(bus.transactions, [dict.fromkeys(P.SETTINGS, 'nothing')])
            self.assertTrue(P.Settings(bus, tmp).restore())
            self.assertEqual(bus.get_actions(), original)
            self.assertEqual(bus.transactions[-1], original)

    def test_any_read_failure_saves_and_sets_nothing(self):
        for key in P.SETTINGS:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                bus = FakeDesktop()
                values = bus.get_actions(); values[key] = None
                bus.get_actions = Mock(return_value=values)
                settings = P.Settings(bus, tmp, emit=lambda _: None)
                self.assertFalse(settings.take())
                self.assertFalse(settings.path.exists())
                self.assertFalse(settings.taken)
                self.assertEqual(bus.transactions, [])

    def test_legacy_value_restores_only_power_and_bad_json_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = FakeDesktop(action='nothing'); settings = P.Settings(bus, tmp)
            settings.path.write_text('interactive\n')
            self.assertTrue(settings.restore())
            self.assertEqual(bus.action, 'interactive')
            self.assertEqual(bus.transactions, [])
            self.assertEqual(bus.lid_actions, dict.fromkeys(P.LID_SETTINGS, 'suspend'))
            for bad in ['{}', '[]', '{bad', json.dumps({**bus.get_actions(), 'extra': 'nothing'}),
                        json.dumps({**bus.get_actions(), P.LID_SETTINGS[0]: None})]:
                settings.path.write_text(bad); bus.calls.clear()
                self.assertTrue(settings.restore())
                self.assertEqual(bus.calls, [])

    def test_desktop_reads_every_key_and_failure_never_yields_partial_map(self):
        for failed in [None, *P.SETTINGS]:
            def run(argv, **kwargs):
                return Mock(returncode=1 if argv[-1] == failed else 0, stdout="'suspend'")
            runner = Mock(side_effect=run); bus = P.Desktop(runner, lambda _: None)
            values = bus.get_actions()
            self.assertEqual(values, None if failed else dict.fromkeys(P.SETTINGS, 'suspend'))
            self.assertEqual([c.args[0][-1] for c in runner.call_args_list], list(P.SETTINGS))

    def test_transaction_uses_bounded_subprocess_no_shell_and_no_output_forwarding(self):
        runner = Mock(return_value=Mock(returncode=1, stdout='secret', stderr='secret'))
        logs = []; bus = P.Desktop(runner, logs.append)
        self.assertFalse(bus.set_actions(dict.fromkeys(P.SETTINGS, 'nothing')))
        self.assertEqual(runner.call_args.args[0][:2], ['gjs', '-c'])
        self.assertEqual(runner.call_args.kwargs['timeout'], 5)
        self.assertNotIn('shell', runner.call_args.kwargs)
        self.assertNotIn('secret', str(logs))

    @unittest.skipUnless(shutil.which('gjs') and shutil.which('glib-compile-schemas'), 'GJS/GIO tools required')
    def test_real_gio_transaction_memory_backend_success_and_staging_failure(self):
        # A fresh private schema + memory backend cannot write real desktop settings.
        schema = '<schemalist><schema id="org.gnome.settings-daemon.plugins.power" path="/test/">'
        for key in P.SETTINGS:
            schema += f'<key name="{key}" type="s"><choices><choice value="suspend"/><choice value="nothing"/></choices><default>\'suspend\'</default></key>'
        schema += '</schema></schemalist>'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'test.gschema.xml').write_text(schema)
            subprocess.run(['glib-compile-schemas', tmp], check=True, capture_output=True)
            for failure in ['none', 'readonly', 'stage', 'range', 'extra']:
                with self.subTest(failure=failure):
                    prefix = r'''
const TestGio = imports.gi.Gio;
let exitCode = 0;
imports.system.exit = code => { exitCode = code; };
const originalSet = TestGio.Settings.prototype.set_string;
let staged = 0;
TestGio.Settings.prototype.set_string = function(key, value) {
    if (TEST_FAILURE === 'stage' && ++staged === 2) return false;
    return originalSet.call(this, key, value);
};
if (TEST_FAILURE === 'readonly')
    TestGio.Settings.prototype.is_writable = key => key !== 'lid-close-ac-action';
'''.replace('TEST_FAILURE', json.dumps(failure))
                    suffix = '''
const check = new TestGio.Settings({schema_id: 'org.gnome.settings-daemon.plugins.power'});
print(JSON.stringify({exitCode, values: ['power-button-action','lid-close-battery-action','lid-close-ac-action'].map(k => check.get_string(k))}));
'''
                    values = dict.fromkeys(P.SETTINGS, 'nothing')
                    if failure == 'range': values[P.LID_SETTINGS[0]] = 'invalid'
                    if failure == 'extra': values['extra'] = 'nothing'
                    result = subprocess.run(['gjs', '-c', prefix + P.SETTINGS_TRANSACTION + suffix, json.dumps(values)],
                        env={**os.environ, 'GSETTINGS_BACKEND': 'memory', 'GSETTINGS_SCHEMA_DIR': tmp},
                        capture_output=True, text=True, timeout=5, check=True)
                    data = json.loads(result.stdout)
                    self.assertEqual(data['exitCode'], 0 if failure == 'none' else 1)
                    self.assertEqual(data['values'], ['nothing' if failure == 'none' else 'suspend'] * 3)


class PackagingTests(unittest.TestCase):
    def test_unit_all_keys_and_rule(self):
        root = TOOL / 'device-rootfs'
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.optionxform = str
        cfg.read(root / 'etc/systemd/user/dby-w09-powerkey.service')
        self.assertEqual(dict(cfg['Unit']), {'Description': 'DBY-W09 session power key',
                         'After': 'graphical-session.target', 'PartOf': 'graphical-session.target',
                         'StartLimitIntervalSec': '0', 'ConditionUser': '!root'})
        self.assertEqual(dict(cfg['Service']), {'Type': 'simple',
            'ExecStart': '/run/dby-device/bin/dby-w09-powerkey',
            'ExecStopPost': '/run/dby-device/bin/dby-w09-powerkey --restore',
            'Restart': 'on-failure', 'RestartSec': '2'})
        self.assertEqual(dict(cfg['Install']), {'WantedBy': 'default.target'})
        rule = root / 'etc/udev/rules.d/70-dby-w09-powerkey.rules'
        lines = [line for line in rule.read_text().splitlines() if line and not line.startswith('#')]
        self.assertEqual(lines, ['SUBSYSTEM=="input", KERNEL=="event[0-9]*", ATTRS{name}=="pm8941_pwrkey", TAG+="uaccess"',
                                 'SUBSYSTEM=="input", KERNEL=="event[0-9]*", ATTRS{name}=="hall-lid", TAG+="uaccess"'])
        self.assertLess(int(rule.name.split('-')[0]), 73)
        self.assertTrue(os.access(SOURCE, os.X_OK))

    def test_existing_builder_preserves_system_and_user_wants(self):
        source = TOOL / 'device-rootfs'
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            overlay(tree, True)
            links = (source / 'wants-links.txt').read_text().splitlines()
            self.assertIn('systemd/user/default.target.wants/dby-w09-powerkey.service', links)
            for name in links:
                target = tree / 'etc' / name
                self.assertTrue(target.is_symlink())
                self.assertEqual(os.readlink(target), '../' + target.name)
                self.assertEqual(target.read_bytes(), (source / 'etc' / name).read_bytes())
            configs = sorted(p.relative_to(tree / 'etc').as_posix() for p in (tree / 'etc').rglob('*')
                             if p.is_file() or p.is_symlink())
            # Display watchdog autostart removed: 28 public configs, 29 with private input.
            self.assertEqual(len(configs), 28)
            self.assertIn('systemd/logind.conf.d/20-dby-w09-powerkey.conf', configs)
            self.assertIn('modprobe.d/11-dby-venus.conf', configs)
            self.assertFalse(any(name.startswith('bin/') for name in configs))

    def test_no_forbidden_input_or_write_api(self):
        text = SOURCE.read_text()
        for forbidden in ('EVIOCGRAB', 'sudo', 'systemctl', 'poweroff'):
            self.assertNotIn(forbidden, text)
        tree = ast.parse(text)
        writes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == 'open':
                    writes.append(ast.unparse(node))
        self.assertEqual(set(writes), {"path.open('rb', buffering=0)",
                                      'os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)',
                                      "find_lid().open('rb', buffering=0)"})
        ioctls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == 'ioctl']
        self.assertEqual(set(ioctls), {'fcntl.ioctl(fd, 2147566875, bits, True)',
                                     "fcntl.ioctl(fd, 1074021792, struct.pack('i', time.CLOCK_MONOTONIC))"})
        self.assertEqual(P.EVENT.size, 24)


if __name__ == '__main__':
    unittest.main()
