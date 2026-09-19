import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch


SOURCE = Path(__file__).resolve().parents[1] / 'files' / 'k3s-kubelet-watchdog.py'
SPEC = importlib.util.spec_from_file_location('watchdog', SOURCE)
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def active_status(**overrides):
    return dict(ActiveState='active', SubState='running', MainPID=123,
                InvocationID='agent-one', ExecMainStartTimestampMonotonic=100,
                Restart='always', KillMode='process') | overrides


class WatchdogChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.json'
        self.state = watchdog.fresh_state()
        self.state.update(boot_id='boot-one', invocation='agent-one')
        self.status = active_status()
        self.status_mock = patch.object(watchdog, 'service_status', return_value=self.status).start()
        self.probe_mock = patch.object(watchdog, 'probe', return_value=False).start()
        self.recovery_mock = patch.object(watchdog, 'recover', side_effect=lambda _u, _s, charge: charge()).start()
        self.addCleanup(patch.stopall)

    def check(self, unit='k3s-agent.service', mode='recover', now=10000, monotonic=1000, boot='boot-one'):
        return watchdog.check(unit, mode, self.state, self.path, now, monotonic, boot)

    def test_healthy_probe_clears_failures(self):
        self.state['failures'] = 2
        self.probe_mock.return_value = True
        self.assertEqual(self.check(), (1, False))
        self.assertEqual(self.state['failures'], 0)
        self.probe_mock.assert_called_once_with(10248)
        self.recovery_mock.assert_not_called()

    def test_secure_probe_is_fallback(self):
        self.probe_mock.side_effect = [False, True]
        self.assertEqual(self.check(), (1, False))
        self.assertEqual(self.probe_mock.call_args_list, [call(10248), call(10250)])

    def test_three_failures_recover_and_persist_budget_first(self):
        for count in (1, 2):
            self.assertEqual(self.check(), (0, False))
            self.assertEqual(self.state['failures'], count)
            self.recovery_mock.assert_not_called()
        self.assertEqual(self.check(), (0, False))
        self.recovery_mock.assert_called_once()
        self.assertEqual(self.state['failures'], 0)
        persisted = watchdog.load_state(self.path)
        self.assertEqual(persisted['recoveries'], [10000])
        self.assertEqual(persisted['recovery_total'], 1)
        self.assertEqual(persisted['last_recovery'], 10000)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_transient_failure_does_not_accumulate(self):
        self.check()
        self.probe_mock.return_value = True
        self.check()
        self.probe_mock.return_value = False
        self.check()
        self.check()
        self.recovery_mock.assert_not_called()

    def test_startup_grace_does_not_probe(self):
        self.state['failures'] = 2
        self.assertEqual(self.check(monotonic=279), (-1, False))
        self.assertEqual(self.state['failures'], 0)
        self.probe_mock.assert_not_called()
        self.recovery_mock.assert_not_called()

    def test_inactive_or_stopping_service_is_untouched(self):
        for active, sub in [('inactive', 'dead'), ('deactivating', 'stop-sigterm'), ('activating', 'start')]:
            with self.subTest(active=active):
                self.status.update(ActiveState=active, SubState=sub)
                self.state['failures'] = 9
                self.assertEqual(self.check(), (-1, False))
        self.probe_mock.assert_not_called()
        self.recovery_mock.assert_not_called()

    def test_control_plane_never_recovers_even_if_misconfigured(self):
        self.state['failures'] = 2
        self.assertEqual(self.check(unit='k3s.service', mode='recover'), (0, False))
        self.recovery_mock.assert_not_called()

    def test_worker_observe_mode_never_recovers(self):
        self.state['failures'] = 2
        self.assertEqual(self.check(mode='observe'), (0, False))
        self.recovery_mock.assert_not_called()

    def test_new_invocation_or_boot_resets_failures_but_not_budget(self):
        for field in ('invocation', 'boot_id'):
            with self.subTest(field=field):
                self.state.update(failures=2, recoveries=[9500], recovery_total=1)
                self.state[field] = 'previous'
                self.check()
                self.assertEqual(self.state['failures'], 1)
                self.assertEqual(self.state['recoveries'], [9500])
        self.recovery_mock.assert_not_called()

    def test_replacement_during_probes_is_skipped(self):
        self.state['failures'] = 2
        self.status_mock.side_effect = [self.status, active_status(MainPID=999, InvocationID='replacement')]
        self.assertEqual(self.check(), (-1, False))
        self.assertEqual(self.state['failures'], 0)
        self.recovery_mock.assert_not_called()

    def test_budget_blocks_until_old_attempt_expires(self):
        self.state.update(failures=2, recoveries=[9000, 9500], recovery_total=2)
        self.assertEqual(self.check(), (0, True))
        self.recovery_mock.assert_not_called()
        self.assertEqual(self.check(now=12601), (0, False))
        self.assertEqual(self.state['recoveries'], [9500, 12601])
        self.assertEqual(self.state['recovery_total'], 3)

    def test_clock_rollback_keeps_budget(self):
        self.state.update(failures=2, recoveries=[11000, 12000], recovery_total=2)
        self.assertEqual(self.check(), (0, True))
        self.recovery_mock.assert_not_called()

    def test_unknown_start_timestamp_fails_closed(self):
        for started in (0, 1001):
            with self.subTest(started=started):
                self.status['ExecMainStartTimestampMonotonic'] = started
                with self.assertRaises(ValueError):
                    self.check()
        self.probe_mock.assert_not_called()
        self.recovery_mock.assert_not_called()


class ProcessRecovery(unittest.TestCase):
    def setUp(self):
        self.status = active_status()
        self.status_mock = patch.object(watchdog, 'service_status', return_value=self.status).start()
        self.open_mock = patch.object(watchdog.os, 'pidfd_open', return_value=71).start()
        self.close_mock = patch.object(watchdog.os, 'close').start()
        self.signal_mock = patch.object(watchdog.signal, 'pidfd_send_signal').start()
        self.poll_mock = patch.object(watchdog.select, 'poll').start().return_value
        self.poll_mock.poll.return_value = []
        self.charge = Mock()
        self.addCleanup(patch.stopall)

    def test_quit_then_kill_original_after_dump_grace(self):
        self.assertTrue(watchdog.recover('k3s-agent.service', self.status, self.charge))
        self.charge.assert_called_once()
        self.assertEqual(self.signal_mock.call_args_list, [call(71, signal.SIGQUIT), call(71, signal.SIGKILL)])
        self.poll_mock.poll.assert_called_once_with(20000)
        self.close_mock.assert_called_once_with(71)

    def test_normal_exit_after_quit_does_not_kill(self):
        self.poll_mock.poll.return_value = [(71, 1)]
        watchdog.recover('k3s-agent.service', self.status, self.charge)
        self.signal_mock.assert_called_once_with(71, signal.SIGQUIT)

    def test_replacement_before_recovery_gets_no_signal(self):
        self.status_mock.return_value = active_status(MainPID=999, InvocationID='replacement')
        self.assertFalse(watchdog.recover('k3s-agent.service', self.status, self.charge))
        self.charge.assert_not_called()
        self.signal_mock.assert_not_called()

    def test_same_pid_new_invocation_after_quit_is_not_killed(self):
        self.status_mock.side_effect = [self.status, self.status, active_status(InvocationID='replacement')]
        watchdog.recover('k3s-agent.service', self.status, self.charge)
        self.signal_mock.assert_called_once_with(71, signal.SIGQUIT)

    def test_replacement_after_budget_write_gets_no_signal(self):
        self.status_mock.side_effect = [self.status, active_status(MainPID=999, InvocationID='replacement')]
        self.assertFalse(watchdog.recover('k3s-agent.service', self.status, self.charge))
        self.charge.assert_called_once()
        self.signal_mock.assert_not_called()

    def test_budget_write_failure_sends_no_signal(self):
        self.charge.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            watchdog.recover('k3s-agent.service', self.status, self.charge)
        self.signal_mock.assert_not_called()

    def test_control_plane_rejected_inside_signal_function(self):
        with self.assertRaises(ValueError):
            watchdog.recover('k3s.service', self.status, self.charge)
        self.open_mock.assert_not_called()
        self.signal_mock.assert_not_called()

    def test_disabled_restart_rejected(self):
        self.status['Restart'] = 'no'
        with self.assertRaises(ValueError):
            watchdog.recover('k3s-agent.service', self.status, self.charge)
        self.open_mock.assert_not_called()
        self.signal_mock.assert_not_called()

    def test_process_group_kill_mode_rejected(self):
        self.status['KillMode'] = 'control-group'
        with self.assertRaises(ValueError):
            watchdog.recover('k3s-agent.service', self.status, self.charge)
        self.open_mock.assert_not_called()
        self.signal_mock.assert_not_called()

    def test_kill_mode_changed_after_budget_write_sends_no_signal(self):
        self.status_mock.side_effect = [self.status, active_status(KillMode='control-group')]
        self.assertFalse(watchdog.recover('k3s-agent.service', self.status, self.charge))
        self.signal_mock.assert_not_called()

    def test_signal_target_exit_is_safe(self):
        self.signal_mock.side_effect = ProcessLookupError()
        self.assertFalse(watchdog.recover('k3s-agent.service', self.status, self.charge))
        self.signal_mock.assert_called_once_with(71, signal.SIGQUIT)


@unittest.skipUnless(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'requires Linux pidfds')
class RealPidfdRecovery(unittest.TestCase):
    def test_real_process_recovers_or_escalates_without_other_processes(self):
        for quit_handler, exit_code in [('lambda *_: sys.exit(0)', 0), ('signal.SIG_IGN', -signal.SIGKILL)]:
            with self.subTest(quit_handler=quit_handler):
                program = ('import signal, sys, time; '
                           'signal.signal(signal.SIGQUIT, ' + quit_handler + '); '
                           'print("ready", flush=True); time.sleep(60)')
                process = subprocess.Popen([sys.executable, '-c', program], stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(), 'ready')
                    status = active_status(MainPID=process.pid)
                    with patch.object(watchdog, 'service_status', return_value=status), patch.object(watchdog, 'DUMP_GRACE', 0.05):
                        self.assertTrue(watchdog.recover('k3s-agent.service', status, Mock()))
                    self.assertEqual(process.wait(timeout=5), exit_code)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    process.stdout.close()


class StateAndMetrics(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_invalid_state_does_not_reset_recovery_budget(self):
        state_path = self.root / 'state.json'
        state_path.write_text('{broken')
        metrics = self.root / 'watchdog.prom'
        with patch.object(watchdog, 'STATE_DIR', self.root), patch.object(watchdog, 'METRICS_FILE', metrics), patch.object(sys, 'argv', ['watchdog', '--unit', 'k3s-agent.service', '--mode', 'recover']), patch.object(watchdog, 'recover') as recovery:
            self.assertEqual(watchdog.main(), 1)
            recovery.assert_not_called()
        self.assertEqual(state_path.read_text(), '{broken')
        self.assertIn('h3xinfra_kubelet_watchdog_recovery_blocked{', metrics.read_text())
        self.assertIn('mode="recover"} 1', metrics.read_text())

    def test_checker_error_is_unhealthy_on_observe_only_servers(self):
        metrics = self.root / 'watchdog.prom'
        with patch.object(watchdog, 'STATE_DIR', self.root), patch.object(watchdog, 'METRICS_FILE', metrics), patch.object(sys, 'argv', ['watchdog', '--unit', 'k3s.service', '--mode', 'recover']), patch.object(watchdog, 'service_status', side_effect=ValueError('missing systemd status')):
            self.assertEqual(watchdog.main(), 1)
        samples = dict(line.split(' ', 1) for line in metrics.read_text().splitlines() if not line.startswith('#'))
        healthy = next(value for key, value in samples.items() if key.startswith('h3xinfra_kubelet_watchdog_healthy{'))
        self.assertEqual(healthy, '0')
        self.assertIn('mode="observe"', metrics.read_text())

    def test_metrics_are_readable_and_atomic(self):
        metrics = self.root / 'watchdog.prom'
        with patch.object(watchdog.socket, 'gethostname', return_value='worker.example.test'):
            watchdog.write_metrics(metrics, 'k3s-agent.service', 'recover', watchdog.fresh_state(), 1, False, 12345)
        content = metrics.read_text()
        self.assertIn('node="worker",unit="k3s-agent.service",mode="recover"', content)
        self.assertIn('# TYPE h3xinfra_kubelet_watchdog_recovery_total counter', content)
        self.assertEqual(metrics.stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(self.root.iterdir()), [metrics])

    def test_invalid_state_shapes_fail_closed(self):
        path = self.root / 'state.json'
        for state in [[], {}, watchdog.fresh_state() | {'recoveries': 'wrong'}, watchdog.fresh_state() | {'recoveries': [float('nan')]}, watchdog.fresh_state() | {'recovery_total': -1}]:
            with self.subTest(state=state):
                path.write_text(json.dumps(state))
                with self.assertRaises(ValueError):
                    watchdog.load_state(path)


class HealthProbes(unittest.TestCase):
    def test_plain_probe_requires_ok_body(self):
        with patch.object(watchdog.http.client, 'HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'ok\n'
            self.assertTrue(watchdog.probe(10248))
            response.read.return_value = b'unhealthy'
            self.assertFalse(watchdog.probe(10248))

    def test_secure_probe_accepts_auth_rejection_but_not_server_errors(self):
        with patch.object(watchdog.http.client, 'HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            for code, expected in [(200, True), (401, True), (403, True), (500, False), (503, False)]:
                with self.subTest(code=code):
                    response.status = code
                    self.assertEqual(watchdog.probe(10250), expected)

    def test_timeout_is_a_failed_probe_and_restores_alarm(self):
        handler = signal.getsignal(signal.SIGALRM)
        with patch.object(watchdog.http.client, 'HTTPConnection', side_effect=TimeoutError()):
            self.assertFalse(watchdog.probe(10248))
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
