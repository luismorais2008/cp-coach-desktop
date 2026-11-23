import importlib.util
import base64
import fnmatch
import json
import io
from contextlib import nullcontext
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import time
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('coach', Path(__file__).with_name('cp-coach.py'))
coach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coach)


class BlockerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(coach, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        strict_state = patch.object(coach.helper, 'STRICT_STATE', self.root / 'strict.json')
        strict_state.start()
        self.addCleanup(strict_state.stop)
        self.network_patch = patch.object(coach.Blocker, 'network_command', return_value=True)
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        control = patch.object(coach.Blocker, 'session_control', return_value=None)
        control.start()
        self.addCleanup(control.stop)
        self.blocker = coach.Blocker()

    def test_keyless_sites_are_recorded_from_history(self):
        sites = ('heck.ai', 'chat.akash.network', 'chatbot.app', 'aichatting.net', 'unlimitedai.org', 'metaso.cn', 'exa.ai', 'morphic.sh', 'arena.ai', 'lmarena.ai', 'qwen-qwen3-demo.hf.space', 'random-demo.hf.space', 'random-demo.gradio.live')
        self.blocker.start('practice-no-ai', 120, strictness='web')
        visits = [('https://' + host + '/', time.time()) for host in sites]
        with patch.object(coach, 'history_databases', return_value=[('/profile/places.sqlite', 'Firefox')]), patch.object(coach, 'history_visits', return_value=visits):
            self.blocker.poll()
        logged = {v['url'] for v in self.blocker.state['current']['violations'] if v['kind'] == 'histórico'}
        expected_domains = {coach.history_domain(url) for url, _ in visits}
        self.assertEqual({coach.history_domain(url) for url in logged}, expected_domains)
        self.assertEqual(len(logged), len(expected_domains))

    def test_history_and_session_end(self):
        self.blocker.start('practice-no-ai', 120)
        path = self.root / 'History'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE urls(id INTEGER, url TEXT); CREATE TABLE visits(url INTEGER, visit_time INTEGER);')
            db.execute('INSERT INTO urls VALUES(1, ?)', ('https://chatgpt.com/c/test',))
            db.execute('INSERT INTO visits VALUES(1, ?)', (int((time.time() + 11644473600) * 1e6),))
        with patch.object(coach, 'history_databases', return_value=[(str(path), 'Fake Chrome')]):
            self.blocker.poll()
            self.blocker.poll()
            visits = [v for v in self.blocker.state['current']['violations'] if v['kind'] == 'histórico']
            self.assertEqual(len(visits), 1)
            print('Fake Chrome detected:', json.dumps(visits, ensure_ascii=False))
            self.blocker.stop('')
        self.assertIsNone(self.blocker.state['current'])
        self.assertEqual(len(self.blocker.state['sessions']), 1)

    def test_all_modes_are_timed_logged_and_exclusive(self):
        for mode in ('practice-ai', 'practice-no-ai', 'contest'):
            self.blocker.start(mode, 1)
            current = self.blocker.state['current']
            self.assertEqual(current['deadline'], current['start'] + 60)
            with self.assertRaises(ValueError):
                self.blocker.start('practice-ai', 1)
            current['deadline'] = time.time() - 1
            with patch.object(coach, 'history_databases', return_value=[]):
                self.blocker.poll()
            self.assertIsNone(self.blocker.state['current'])
            self.assertEqual(self.blocker.state['sessions'][-1]['end_reason'], 'time')
        for minutes in (0, -1, 10081, None):
            with self.assertRaises(ValueError):
                self.blocker.start('practice-ai', minutes)

    def test_idle_preferences_survive_monitor_restart_and_reject_invalid_changes(self):
        self.blocker.set_preferences('practice-no-ai', 7)
        reopened = coach.Blocker()
        self.assertEqual(reopened.state['idle'], {'mode': 'practice-no-ai', 'minutes': 7})
        for mode, minutes in (('unknown', 7), ('contest', 0), ('contest', None), ('contest', True), ('contest', float('nan'))):
            with self.assertRaises(ValueError):
                reopened.set_preferences(mode, minutes)
        self.assertEqual(reopened.state['idle'], {'mode': 'practice-no-ai', 'minutes': 7})
        reopened.start('practice-ai', 2)
        with self.assertRaises(ValueError):
            reopened.set_preferences('contest', 5)
        self.assertEqual(reopened.state['idle'], {'mode': 'practice-ai', 'minutes': 2})

    def test_strictness_and_allowed_are_persisted_and_locked(self):
        self.blocker.set_allowed(['EXAMPLE.ORG', '*.example.com'])
        self.blocker.set_preferences('contest', 7, 'web')
        reopened = coach.Blocker()
        self.assertNotIn('strictness', reopened.state['idle'])
        self.assertEqual(reopened.state['allowed'], ['*.example.com', 'example.org'])
        reopened.start('contest', 7)
        current = reopened.state['current']
        self.assertEqual(current['strictness'], 'strict')
        self.assertEqual(current['allowed'], reopened.state['allowed'])
        self.assertIsNot(current['allowed'], reopened.state['allowed'])
        for edit in (lambda: reopened.set_allowed(['example.net']), lambda: reopened.set_preferences('contest', 7, 'web')):
            with self.assertRaises(ValueError):
                edit()
        reopened.stop('TERMINAR CONTEST')
        self.assertEqual(reopened.state['sessions'][-1]['strictness'], 'strict')
        reopened.set_allowed(['example.net'])
        reopened.start('practice-no-ai', 2, 'web')
        self.assertEqual(reopened.state['current']['allowed'], ['example.net'])
        self.assertEqual(reopened.state['sessions'][-1]['allowed'], ['*.example.com', 'example.org'])

    def test_invalid_strictness_and_allowlist_leave_state_unchanged(self):
        original = json.dumps(self.blocker.state, sort_keys=True)
        for call in (lambda: self.blocker.start('contest', 1, 'other'), lambda: self.blocker.set_preferences('contest', 1, None), lambda: self.blocker.set_allowed([]), lambda: self.blocker.set_allowed(['https://example.org'])):
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(json.dumps(self.blocker.state, sort_keys=True), original)

    def test_strict_dns_refresh_is_periodic_not_on_every_poll(self):
        self.blocker.start('contest', 7)
        self.blocker.last_refresh = time.monotonic()
        with patch.object(Path, 'is_file', return_value=True), patch.object(self.blocker, 'network_command', return_value=True) as command:
            self.blocker.check_network()
            self.assertEqual([call.args for call in command.call_args_list], [('status',)])
            self.blocker.last_refresh -= 181
            self.blocker.check_network()
            self.assertEqual(command.call_args_list[-1].args, ('refresh',))

    def test_firefox_xdg_profile_registries_and_wal(self):
        home = self.root / 'home'
        registry = home / '.config/mozilla/firefox'
        registry.mkdir(parents=True)
        external = self.root / 'external-profile'
        external.mkdir()
        path = external / 'places.sqlite'
        now = time.time()
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE moz_places(id INTEGER, url TEXT); CREATE TABLE moz_historyvisits(place_id INTEGER, visit_date INTEGER);')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('INSERT INTO moz_places VALUES(1, ?)', ('https://duck.ai/?marker=both-browsers',))
            db.execute('INSERT INTO moz_historyvisits VALUES(1, ?)', (int(now * 1e6),))
            db.commit()
            (registry / 'profiles.ini').write_text('[Profile0]\nIsRelative=0\nPath=' + str(external) + '\n')
            (registry / 'installs.ini').write_text('[install]\nDefault=' + str(external) + '\n')
            with patch.object(Path, 'home', return_value=home):
                matches = [(p, b) for p, b in coach.history_databases() if p == str(path)]
            self.assertEqual(len(matches), 1)
            self.assertEqual(coach.history_visits(str(path), now - 1, now + 1)[0][0], 'https://duck.ai/?marker=both-browsers')
            (registry / 'profiles.ini').write_text('invalid registry')
            with patch.object(Path, 'home', return_value=home):
                self.assertIn((str(path), 'Firefox: external-profile'), coach.history_databases())
            (registry / 'installs.ini').unlink()
            with patch.object(Path, 'home', return_value=home):
                self.assertFalse(any(p == str(path) for p, _ in coach.history_databases()))

    def test_network_changes_restore_without_waiting_for_history_poll(self):
        from unittest.mock import Mock
        self.blocker.start('practice-no-ai', 1)
        watcher = Mock()
        def wait(timeout):
            if watcher.wait.call_count > 1:
                self.blocker.shutdown.set()
            return True
        watcher.wait.side_effect = wait
        with patch.object(coach, 'NetworkWatcher', return_value=watcher), patch.object(self.blocker, 'check_network') as restore, patch.object(self.blocker, 'poll') as poll, patch.object(self.blocker, 'scan_processes'), patch.object(self.blocker, 'rescan_finished'):
            self.blocker.run()
        restore.assert_called_once()
        poll.assert_not_called()
        watcher.close.assert_called_once()

    def test_custom_profile_stays_known_for_late_rescan_after_browser_closes(self):
        self.blocker.start('practice-no-ai', 1)
        with patch.object(coach, 'history_databases', return_value=[('/custom/places.sqlite', 'Firefox: custom')]), patch.object(coach, 'history_visits', return_value=[]):
            self.blocker.scan_history(self.blocker.state['current'], time.time())
        self.blocker.save()
        reopened = coach.Blocker()
        with patch.object(coach, 'history_databases', return_value=[]), patch.object(coach, 'history_visits', return_value=[('https://duck.ai/?late-marker', time.time())]):
            reopened.scan_history(reopened.state['current'], time.time())
        self.assertEqual(reopened.state['current']['violations'][-1]['url'], 'https://duck.ai/?late-marker')

    def test_network_watcher_stat_fallback_create_replace_delete(self):
        target = self.root / 'policy.json'
        with patch.object(coach.helper, 'policy_targets', return_value=[(target, 'chrome')]), patch.object(coach.ctypes, 'CDLL', side_effect=OSError):
            watcher = coach.NetworkWatcher()
        try:
            self.assertEqual(watcher.fd, -1)
            self.assertFalse(watcher.wait(0))
            target.write_text('{}')
            self.assertTrue(watcher.wait(0))
            replacement = self.root / 'replacement'
            replacement.write_text('{}')
            replacement.replace(target)
            self.assertTrue(watcher.wait(0))
            target.unlink()
            self.assertTrue(watcher.wait(0))
            self.assertFalse(watcher.wait(0))
        finally:
            watcher.close()

    def test_google_ai_history_chrome_and_firefox(self):
        self.blocker.start('practice-no-ai', 1)
        urls = ['https://www.google.com/search?q=binary+search+codeforces', 'https://www.google.com/search?udm=50&q=binary', 'https://www.google.com/search?q=binary&udm=50']
        databases = []
        for browser, filename in (('Chrome', 'History'), ('Firefox', 'places.sqlite')):
            path = self.root / filename
            timestamp = time.time()
            with sqlite3.connect(path) as db:
                if browser == 'Chrome':
                    db.executescript('CREATE TABLE urls(id INTEGER, url TEXT); CREATE TABLE visits(url INTEGER, visit_time INTEGER);')
                    for i, url in enumerate(urls):
                        db.execute('INSERT INTO urls VALUES(?,?)', (i, url))
                        db.execute('INSERT INTO visits VALUES(?,?)', (i, int((timestamp + 11644473600) * 1e6)))
                else:
                    db.executescript('CREATE TABLE moz_places(id INTEGER, url TEXT); CREATE TABLE moz_historyvisits(place_id INTEGER, visit_date INTEGER);')
                    for i, url in enumerate(urls):
                        db.execute('INSERT INTO moz_places VALUES(?,?)', (i, url))
                        db.execute('INSERT INTO moz_historyvisits VALUES(?,?)', (i, int(timestamp * 1e6)))
            databases.append((str(path), browser))
        with patch.object(coach, 'history_databases', return_value=databases):
            self.blocker.poll()
        visits = self.blocker.state['current']['violations']
        self.assertEqual({v['browser'] for v in visits}, {'Chrome', 'Firefox'})
        self.assertEqual(len(visits), 2)
        self.assertTrue(all('udm=50' in v['url'] for v in visits))
        print('Google history: Chrome + Firefox AI Mode flagged; normal search ignored; redirects deduplicated')

    def test_foreign_uid_checked_before_exe_and_permission_noise_ignored(self):
        from unittest.mock import Mock
        path = Mock()
        path.name = '999'
        path.stat.return_value.st_uid = os.getuid() + 1
        with patch.object(coach.os, 'readlink') as readlink:
            self.assertIsNone(coach.process_info(path))
            readlink.assert_not_called()
        self.blocker.start('practice-no-ai', 1)
        with patch.object(Path, 'glob', return_value=[Path('/proc/999')]), patch.object(coach, 'process_info', side_effect=PermissionError('exe')), patch.object(coach.time, 'monotonic', side_effect=[100, 106]):
            self.blocker.scan_processes()
            self.blocker.scan_processes()
        self.assertEqual(self.blocker.monitor_errors, [])

    def test_repeated_matched_process_warning_deduplicated(self):
        info = dict(pid=99, start='1')
        self.blocker.process_warning(info, PermissionError('denied'))
        self.blocker.process_warning(info, PermissionError('denied'))
        self.assertEqual(len(self.blocker.monitor_errors), 1)

    def test_live_network_check_includes_policy_layer(self):
        self.network_patch.stop()
        with patch.object(Path, 'read_text', return_value=coach.helper.BLOCK), patch.object(coach.helper, 'policy_status', return_value=False):
            self.assertFalse(self.blocker.network_command('status'))
            self.assertTrue(self.blocker.network['hosts'])
            self.assertFalse(self.blocker.status()['network']['active'])
        with patch.object(Path, 'read_text', return_value=coach.helper.BLOCK), patch.object(coach.helper, 'policy_status', return_value=True):
            self.assertTrue(self.blocker.network_command('status'))
        self.network_patch.start()

    def test_idle_status_exposes_residual_firewall_and_permission_errors(self):
        self.network_patch.stop()
        coach.helper.STRICT_STATE.write_text('{}')
        response = dict(active=True, nft=True, hosts=True, policies=True, uid=os.getuid(), allowed=['example.org'], reason='')
        with patch.object(coach.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            self.assertTrue(self.blocker.network_command('status'))
            self.assertIn('residual', self.blocker.network['reason'])
        with patch.object(coach.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stderr='not authorized')):
            self.assertFalse(self.blocker.network_command('status'))
            self.assertIn('not authorized', self.blocker.network['reason'])
        self.network_patch.start()

    def test_empty_error_and_contest(self):
        self.blocker.stop('')
        with self.assertRaises(ValueError):
            self.blocker.start('bad', 10)
        with self.assertRaises(ValueError):
            self.blocker.start('contest', 0)
        self.blocker.start('contest', 10)
        with self.assertRaises(ValueError):
            self.blocker.stop('')
        self.blocker.stop('TERMINAR CONTEST')
        self.assertEqual(self.blocker.state['sessions'][0]['violations'][0]['kind'], 'fim antecipado')

    def test_tamper_and_expiration(self):
        self.blocker.start('contest', 1)
        with patch.object(Path, 'is_file', return_value=True), patch.object(self.blocker, 'network_command', side_effect=[False, True]) as command, patch.object(coach, 'history_databases', return_value=[]):
            self.blocker.poll()
            self.assertEqual(command.call_args_list[-1].args, ('apply',))
            self.assertEqual(self.blocker.state['current']['violations'][0]['kind'], 'bloqueio')
        self.blocker.state['current']['deadline'] = time.time() - 1
        with patch.object(coach, 'history_databases', return_value=[]):
            self.blocker.poll()
        self.assertIsNone(self.blocker.state['current'])

    def test_missing_helper_is_not_tamper(self):
        self.blocker.start('practice-no-ai', 120)
        with patch.object(Path, 'is_file', return_value=False), patch.object(coach, 'history_databases', return_value=[]):
            self.blocker.poll()
        self.assertEqual(self.blocker.state['current']['violations'], [])
        self.assertIn('indisponível', self.blocker.network['reason'])

    def test_process_termination_and_escalation(self):
        self.blocker.start('practice-no-ai', 120)
        info = dict(pid=999999, start='1234', process='claude', cmdline='node /opt/@anthropic-ai/claude-code/cli.js')
        with patch.object(Path, 'glob', return_value=[Path('/proc/999999')]), patch.object(coach, 'process_info', return_value=info), patch.object(self.blocker, 'signal_process', return_value=True) as send, patch.object(coach.time, 'monotonic', return_value=100):
            self.blocker.scan_processes()
            send.assert_called_once_with(info, signal.SIGTERM)
            with patch.object(coach.time, 'monotonic', return_value=104):
                self.blocker.scan_processes()
            self.assertEqual(send.call_args.args, (info, signal.SIGKILL))
            self.assertNotEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')
            with patch.object(coach, 'process_info', return_value=None), patch.object(coach.time, 'monotonic', return_value=106):
                self.blocker.scan_processes()
        self.assertEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')

    def test_process_exit_and_ai_mode(self):
        with patch.object(coach, 'process_info') as inspect:
            self.blocker.start('practice-ai', 120)
            self.blocker.scan_processes()
            inspect.assert_not_called()
        self.blocker.stop('')
        self.blocker.start('practice-no-ai', 120)
        info = dict(pid=999999, start='1234', process='claude', cmdline='claude')
        self.blocker.pending_processes['test'] = (1000, info)
        self.blocker.violation('processo', 'test', **info)
        with patch.object(coach, 'process_info', return_value=None), patch.object(Path, 'glob', return_value=[]):
            self.blocker.scan_processes()
        self.assertEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')
        self.assertFalse(self.blocker.pending_processes)

    def test_process_tree_exit_race_is_not_logged_as_terminated(self):
        self.blocker.start('contest', 1)
        child = dict(pid=888888, start='child', process='claude', descendant=True)
        info = dict(pid=999999, start='root', process='claude', tree=[child])
        self.blocker.pending_processes['tree'] = (0, info)
        self.blocker.violation('processo', 'tree', action='SIGTERM enviado')
        with patch.object(Path, 'glob', return_value=[]), patch.object(coach, 'process_tree', return_value=[]), patch.object(coach, 'process_record', return_value=child), patch.object(coach, 'process_info', return_value=info), patch.object(self.blocker, 'signal_process', side_effect=ProcessLookupError):
            self.blocker.scan_processes()
        self.assertIn('tree', self.blocker.pending_processes)
        self.assertNotEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')
        with patch.object(Path, 'glob', return_value=[]), patch.object(coach, 'process_tree', return_value=[]), patch.object(coach, 'process_record', return_value=None), patch.object(coach, 'process_info', return_value=None):
            self.blocker.scan_processes()
        self.assertEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')

    def test_process_identity_safety(self):
        self.assertIsNone(coach.process_info(Path('/proc') / str(os.getpid())))
        with patch.object(Path, 'stat') as stat:
            stat.return_value.st_uid = os.getuid() + 1
            self.assertIsNone(coach.process_info(Path('/proc/999999')))
        info = dict(pid=999999, start='old', process='claude', cmdline='claude')
        with patch.object(coach, 'process_info', return_value=dict(info, start='new')), patch.object(coach.os, 'kill') as kill:
            self.assertFalse(self.blocker.signal_process(info, signal.SIGTERM))
            kill.assert_not_called()

    def history_fixture(self, firefox=False):
        path = self.root / ('places.sqlite' if firefox else 'History')
        with sqlite3.connect(path) as db:
            if firefox:
                db.executescript('CREATE TABLE moz_places(id INTEGER, url TEXT); CREATE TABLE moz_historyvisits(place_id INTEGER, visit_date INTEGER);')
            else:
                db.executescript('CREATE TABLE urls(id INTEGER, url TEXT); CREATE TABLE visits(url INTEGER, visit_time INTEGER);')
        return path

    def add_visit(self, path, url, timestamp):
        with sqlite3.connect(path) as db:
            if path.name == 'places.sqlite':
                ident = db.execute('SELECT COALESCE(MAX(id),0)+1 FROM moz_places').fetchone()[0]
                db.execute('INSERT INTO moz_places VALUES(?, ?)', (ident, url))
                db.execute('INSERT INTO moz_historyvisits VALUES(?, ?)', (ident, int(timestamp * 1e6)))
            else:
                ident = db.execute('SELECT COALESCE(MAX(id),0)+1 FROM urls').fetchone()[0]
                db.execute('INSERT INTO urls VALUES(?, ?)', (ident, url))
                db.execute('INSERT INTO visits VALUES(?, ?)', (ident, int((timestamp + 11644473600) * 1e6)))

    def test_chrome_firefox_chatbots_redirects_and_allowlist(self):
        chrome, firefox = self.history_fixture(), self.history_fixture(True)
        self.blocker.start('practice-no-ai', 120)
        session = self.blocker.state['current']
        start = session['start']
        urls = ['https://chat.z.ai/c/one', 'https://login.chat.z.ai/redirect',
                'https://chat.z.ai/c/two', 'https://api-docs.deepseek.com/',
                'https://help.openai.com/a', 'https://auth.openai.com/authorize',
                'https://platform.deepseek.com/docs/api', 'https://openai.com/',
                'https://anthropic.com/', 'https://platform.openai.com/docs',
                'https://platform.openai.com/playground']
        for path in (chrome, firefox):
            for index, url in enumerate(urls):
                self.add_visit(path, url, start + 1 + index)
            self.add_visit(path, 'https://chat.z.ai/c/later', start + 125)
            self.add_visit(path, 'https://chat.z.ai/c/outside', start - 5)
        with patch.object(coach, 'history_databases', return_value=[(str(chrome), 'Chrome'), (str(firefox), 'Firefox')]):
            self.blocker.scan(start + 140)
            self.blocker.scan(start + 140)
        violations = session['violations']
        self.assertEqual(len(violations), 6)
        for browser in ('Chrome', 'Firefox'):
            visits = [v for v in violations if v['browser'] == browser]
            self.assertEqual(sum(v['domain'] == 'z.ai' for v in visits), 2)
            self.assertEqual(sum(v['url'] == 'https://platform.openai.com/playground' for v in visits), 1)
        print('Chrome + Firefox: chat.z.ai flagged, redirects collapsed, docs/help/auth ignored; 6 expected visits')

    def test_late_history_rescan_and_restart(self):
        chrome, firefox = self.history_fixture(), self.history_fixture(True)
        self.blocker.start('practice-no-ai', 120)
        session = self.blocker.state['current']
        start = session['start']
        databases = [(str(chrome), 'Chrome'), (str(firefox), 'Firefox')]
        with patch.object(coach, 'history_databases', return_value=databases), patch.object(coach.time, 'time', return_value=start + 10):
            self.blocker.stop('')
        self.assertEqual(session['violations'], [])
        for path in (chrome, firefox):
            self.add_visit(path, 'https://chat.z.ai/c/written-late', start + 5)
        with patch.object(coach, 'history_databases', return_value=databases), patch.object(coach.time, 'time', return_value=start + 24):
            self.blocker.rescan_finished()
        self.assertEqual(session['violations'], [])
        with patch.object(coach, 'history_databases', return_value=databases), patch.object(coach.time, 'time', return_value=start + 26):
            self.blocker.rescan_finished()
        self.assertEqual(len(session['violations']), 2)
        self.assertEqual(len(session['history_rescans']), 1)
        for path in (chrome, firefox):
            self.add_visit(path, 'https://kimi.com/c/written-even-later', start + 6)
        with patch.object(coach, 'history_databases', return_value=databases), patch.object(coach.time, 'time', return_value=start + 71):
            resumed = coach.Blocker()
        saved = resumed.state['sessions'][-1]
        self.assertEqual(len(saved['violations']), 4)
        self.assertEqual(saved['history_rescans'], [])
        self.assertIsNone(resumed.state['current'])
        print('Late writes: empty at end, caught at +15s; +60s rescan recovered after restart (Chrome + Firefox)')

    def test_contest_end_modes_schedule_history_rescans(self):
        self.blocker.start('contest', 1)
        self.blocker.stop('TERMINAR CONTEST')
        early = self.blocker.state['sessions'][-1]
        self.assertEqual(early['history_rescans'], [early['end'] + 15, early['end'] + 60])
        self.blocker.start('contest', 1)
        self.blocker.state['current']['deadline'] = time.time() - 1
        self.blocker.poll()
        expired = self.blocker.state['sessions'][-1]
        self.assertEqual(expired['history_rescans'], [expired['end'] + 15, expired['end'] + 60])

    def test_late_rescan_database_error_is_reported(self):
        self.blocker.start('practice-no-ai', 120)
        self.blocker.stop('')
        session = self.blocker.state['sessions'][-1]
        with patch.object(coach, 'history_databases', return_value=[(str(self.root / 'missing'), 'Chrome')]), patch.object(coach.time, 'time', return_value=session['end'] + 16):
            self.blocker.rescan_finished()
        self.assertTrue(self.blocker.monitor_errors)
        self.assertEqual(len(session['history_rescans']), 1)


class MatcherTests(unittest.TestCase):
    def test_appimages_and_native_agents(self):
        for name in ('Jan_0.8.8_amd64.AppImage', 'LM-Studio-0.3.9-x64.AppImage', 'GPT4All.AppImage', 'Msty.AppImage', 'AnythingLLM.AppImage', 'Ollama.AppImage', 'Cherry-Studio-1.0.0.AppImage', 'Chatbox.AppImage'):
            with self.subTest(name=name):
                self.assertIsNotNone(coach.agent_match('/tmp/.mount_test/AppRun', ['AppRun'], '/home/u/' + name))
        for executable, argv in (('/tmp/.mount_Jan123/Jan', ['Jan']), ('/home/u/.local/share/claude/versions/2.1.38', ['claude']), ('/x/vendor/x86_64-unknown-linux-musl/codex/codex', ['codex']), ('/x/copilot-linux-x64/copilot', ['copilot'])):
            self.assertIsNotNone(coach.agent_match(executable, argv))
        self.assertIsNone(coach.agent_match('/tmp/.mount_editor/AppRun', ['AppRun'], '/home/u/TextEditor.AppImage'))
        self.assertIsNone(coach.agent_match('/usr/bin/g++', ['g++', 'Jan.AppImage.cpp']))
        self.assertIsNotNone(coach.agent_match('/tmp/.mount_JanX6aB34/AppRun', ['AppRun']))
        self.assertIsNone(coach.agent_match('/tmp/.mount_EditorX6aB34/AppRun', ['AppRun']))

    def test_agent_matches(self):
        cases = [('/usr/bin/node', ['node', '/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js']),
                 ('/home/u/.local/bin/claude', ['/home/u/.local/bin/claude']),
                 ('/usr/bin/devin', ['devin']), ('/usr/bin/codex', ['codex']),
                 ('/usr/bin/node', ['node', '/x/@openai/codex/bin/codex.js']),
                 ('/usr/bin/node', ['node', '/x/@google/gemini-cli/index.js']),
                 ('/usr/bin/node', ['node', '/x/@github/copilot/index.js']),
                 ('/usr/bin/node', ['node', '/x/@sourcegraph/amp/index.js']),
                 ('/usr/bin/gh', ['gh', 'copilot', 'suggest']),
                 ('/usr/bin/python3', ['python3', '-m', 'aider']),
                 ('/usr/bin/dash', ['/bin/sh', '/tmp/claude'])]
        for executable, argv in cases:
            with self.subTest(argv=argv):
                self.assertIsNotNone(coach.agent_match(executable, argv))

    def test_non_agents(self):
        for argv in (['g++', 'claude.cpp'], ['vim', 'devin-notes.txt'], ['python3', 'cp-coach.py'],
                     ['node', '/x/@anthropic-ai/claude-code-notes/cli.js'], ['bash', '-c', 'claude'],
                     ['python3', 'claude.cpp'], ['gh', 'issue'], ['devin', 'serve-web'],
                     ['devin-remote'], ['studio'], ['lm']):
            with self.subTest(argv=argv):
                self.assertIsNone(coach.agent_match('/usr/bin/' + argv[0], argv))


class EmergencyTests(unittest.TestCase):
    setUp = BlockerTests.setUp
    def event(self, session):
        return dict(id='b' * 32, session=session['id'], uid=1000, start=session['start'], deadline=session['deadline'], time=time.time(), how='helper remove', strict=True, mode=session['mode'])

    def test_early_remove_is_imported_once_with_timestamp(self):
        self.blocker.start('contest', 7)
        event = self.event(self.blocker.state['current'])
        with patch.object(self.blocker, 'session_control', return_value={'active': None, 'events': [event]}):
            self.blocker.reconcile_emergencies()
            self.blocker.reconcile_emergencies()
        self.assertIsNone(self.blocker.state['current'])
        self.assertEqual(len(self.blocker.state['sessions']), 1)
        session = self.blocker.state['sessions'][0]
        self.assertEqual(session['end_reason'], 'emergency')
        self.assertEqual(session['end'], event['time'])
        self.assertEqual(session['violations'][0]['event_time'], event['time'])

    def test_reinstall_without_local_session_imports_retained_journal(self):
        self.blocker.start('practice-no-ai', 7)
        event = self.event(self.blocker.state['current'])
        self.blocker.state['current'] = None
        with patch.object(self.blocker, 'session_control', return_value={'active': None, 'events': [event]}):
            self.blocker.reconcile_emergencies()
        self.assertEqual(self.blocker.state['sessions'][0]['id'], event['session'])
        self.assertEqual(self.blocker.state['sessions'][0]['end_reason'], 'emergency')

    def test_late_recovery_does_not_extend_session_history_past_deadline(self):
        self.blocker.start('practice-no-ai', 7)
        current = self.blocker.state['current']
        event = dict(self.event(current), time=current['deadline'] + 30)
        with patch.object(self.blocker, 'session_control', return_value={'active': None, 'events': [event]}):
            self.blocker.reconcile_emergencies()
        session = self.blocker.state['sessions'][-1]
        self.assertEqual(session['end'], current['deadline'])
        self.assertEqual(session['violations'][0]['event_time'], event['time'])

    def test_heartbeat_gap_keeps_intact_restriction_until_normal_deadline(self):
        for strictness in ('strict', 'web'):
            with self.subTest(strictness=strictness):
                self.blocker.start('contest', 1, strictness)
                current = self.blocker.state['current']
                current['restriction_recorded'] = True
                current['heartbeat'] = time.time() - 30
                self.blocker.save()
                with patch.object(coach.Blocker, 'session_control', return_value={'events': []}) as control, patch.object(coach.Blocker, 'network_command', return_value=True) as network:
                    reopened = coach.Blocker()
                    session = reopened.state['current']
                    self.assertEqual(session['id'], current['id'])
                    self.assertEqual(session['deadline'], current['deadline'])
                    self.assertTrue(session['broken'])
                    self.assertEqual(session['violations'][0]['kind'], 'monitorização interrompida')
                    self.assertGreaterEqual(session['violations'][0]['gap_seconds'], 30)
                    self.assertNotIn('end', session)
                    again = coach.Blocker()
                    self.assertEqual(len(again.state['current']['violations']), 1)
                    self.assertFalse(any(call.args and call.args[0] in ('finish', 'interrupt') for call in control.call_args_list))
                    self.assertFalse(any(call.args == ('remove',) for call in network.call_args_list))
                    with patch.object(coach.time, 'time', return_value=current['deadline'] + .1):
                        again.poll()
                    self.assertIsNone(again.state['current'])
                    finished = again.state['sessions'][-1]
                    self.assertEqual(finished['end_reason'], 'time')
                    self.assertEqual(finished['end'], current['deadline'])
                    self.assertTrue(finished['broken'])
                    self.assertFalse(any(v['kind'] == 'saída de emergência' for v in finished['violations']))
                    control.assert_called_with('finish', {'id': current['id']})
                    self.blocker = again

    def test_missing_block_still_ends_session_as_emergency(self):
        for gap in (0, 30):
            with self.subTest(gap=gap):
                self.blocker.start('practice-no-ai', 7)
                self.blocker.state['current']['heartbeat'] = time.time() - gap
                self.blocker.save()
                with patch.object(coach.Blocker, 'network_command', return_value=False):
                    reopened = coach.Blocker()
                self.assertIsNone(reopened.state['current'])
                session = reopened.state['sessions'][-1]
                self.assertEqual(session['end_reason'], 'emergency')
                self.assertGreaterEqual(session['violations'][0]['gap_seconds'], gap)
                self.blocker = reopened

    def test_intact_block_with_gap_after_deadline_finishes_normally(self):
        self.blocker.start('contest', 1)
        self.blocker.state['current'].update(heartbeat=time.time() - 90, deadline=time.time() - 5)
        self.blocker.save()
        reopened = coach.Blocker()
        self.assertIsNone(reopened.state['current'])
        finished = reopened.state['sessions'][-1]
        self.assertEqual(finished['end_reason'], 'time')
        self.assertTrue(finished['broken'])
        self.assertEqual(finished['violations'][0]['kind'], 'monitorização interrompida')

    def test_empty_or_unavailable_journal_does_not_break_healthy_session(self):
        self.blocker.start('practice-no-ai', 7)
        for value in (None, {'active': None, 'events': []}):
            with patch.object(self.blocker, 'session_control', return_value=value):
                self.blocker.reconcile_emergencies(startup=True)
                self.assertIsNotNone(self.blocker.state['current'])


class RootJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.helper = coach.helper
        for key, value in (('RESTRICTION_STATE', root / 'restriction.json'), ('EMERGENCY_JOURNAL', root / 'journal.json')):
            patched = patch.object(self.helper, key, value)
            patched.start()
            self.addCleanup(patched.stop)
        for key, value in (('safe_path', lambda *a, **k: None), ('atomic_write', lambda path, contents, *a, **k: path.write_bytes(contents)), ('caller_uid', lambda: 1000)):
            patched = patch.object(self.helper, key, value)
            patched.start()
            self.addCleanup(patched.stop)
        now = time.time()
        self.value = dict(id='a' * 32, start=now, deadline=now + 60, mode='contest')

    def test_early_removal_journal_and_normal_end(self):
        self.helper.record_restriction(1000, self.value, True)
        self.helper.end_restriction('helper remove')
        events = self.helper.root_json(self.helper.EMERGENCY_JOURNAL, [])
        self.assertEqual(events[0]['session'], self.value['id'])
        self.assertEqual(events[0]['how'], 'helper remove')
        self.assertFalse(self.helper.RESTRICTION_STATE.exists())
        self.helper.record_restriction(1000, self.value, False)
        self.helper.end_restriction('normal end', self.value['id'])
        self.assertEqual(self.helper.root_json(self.helper.EMERGENCY_JOURNAL, []), events)
        self.helper.record_restriction(1000, self.value, False)
        with patch.object(self.helper.time, 'time', return_value=self.value['deadline'] + 1):
            self.helper.end_restriction('helper remove')
        self.assertEqual(self.helper.root_json(self.helper.EMERGENCY_JOURNAL, []), events)

    def test_empty_journal_and_invalid_metadata_or_completion(self):
        self.assertEqual(self.helper.root_json(self.helper.EMERGENCY_JOURNAL, []), [])
        self.helper.end_restriction('helper remove')
        for value in ({}, dict(self.value, deadline=float('nan')), dict(self.value, id='../escape'), dict(self.value, deadline=time.time()-1), dict(self.value, mode='practice-ai')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.record_restriction(1000, value, True)
        self.helper.record_restriction(1000, self.value, True)
        with self.assertRaises(ValueError):
            self.helper.end_restriction('normal end', 'c' * 32)
        self.assertTrue(self.helper.RESTRICTION_STATE.exists())


class BrowserPolicyTests(unittest.TestCase):
    def test_v8_services_are_blocked_and_classified_for_history(self):
        hosts = 'pollinations.ai text.pollinations.ai image.pollinations.ai gen.pollinations.ai venice.ai api.venice.ai kagi.com models.github.ai models.inference.ai.azure.com router.huggingface.co api-inference.huggingface.co api.cerebras.ai inference.cerebras.ai api.groq.com api.together.xyz api.together.ai together.xyz api.fireworks.ai api.deepinfra.com openrouter.ai api.mistral.ai api.cohere.ai api.cohere.com'.split()
        for host in hosts:
            with self.subTest(host=host):
                self.assertIn(host, coach.helper.DOMAINS)
                self.assertTrue(coach.ai_url('https://' + host + '/?v8=marker'))
                self.assertIn(host, coach.helper.browser_policy('chrome')['URLBlocklist'])
                self.assertIn('*://*.' + host + '/*', coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']['Block'])

    def test_google_web_default_and_navigation_exceptions(self):
        helper = coach.helper
        chrome = helper.browser_policy('chrome', {'HomepageLocation': 'https://example.org/'})
        self.assertTrue(chrome['DefaultSearchProviderEnabled'])
        self.assertEqual(chrome['DefaultSearchProviderSearchURL'], 'https://www.google.com/search?q={searchTerms}&udm=14')
        self.assertEqual(chrome['DefaultSearchProviderName'], helper.GOOGLE_WEB_NAME)
        self.assertEqual(chrome['HomepageLocation'], 'https://example.org/')
        self.assertIn('google.com/search', chrome['URLBlocklist'])
        self.assertIn('google.com/search?udm=14', chrome['URLAllowlist'])
        self.assertNotIn('google.com/search', chrome['URLAllowlist'])
        self.assertLessEqual(len(chrome['URLBlocklist']), 1000)
        firefox = helper.browser_policy('firefox')['policies']
        engines = firefox['SearchEngines']
        self.assertEqual(engines, {'PreventInstalls': True})
        self.assertIn('*://*.google.com/search*', firefox['WebsiteFilter']['Block'])
        patterns = firefox['WebsiteFilter']['Exceptions']
        self.assertFalse(any('google.' in pattern for pattern in patterns))
        self.assertLessEqual(len(firefox['WebsiteFilter']['Block']), 1000)

    def test_firefox_google_mixed_markers_have_no_allow_exception(self):
        policy = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']
        for host in coach.helper.GOOGLE_SEARCH_DOMAINS:
            blocks = [pattern for pattern in policy['Block'] if pattern.startswith('*://*.' + host + '/')]
            self.assertEqual(len(blocks), 4)
            for suffix in ('/search?udm=14&udm=50', '/search?udm=50&udm=14', '/search?q=test&udm=50&sei=x', '/search?udm=%35%30&udm=14', '/search?udm=14&udm=5%30', '/search?udm=%350', '/ai?udm=14', '/async/folif?udm=14', '/async/folwr?udm=14', '/async/folsrch?udm=14'):
                url = 'https://www.' + host + suffix
                self.assertTrue(any(fnmatch.fnmatchcase(url, pattern) for pattern in blocks), url)
                self.assertFalse(any(fnmatch.fnmatchcase(url, pattern) for pattern in policy['Exceptions']), url)
            for suffix in ('/search?udm=14&q=test&sei=x', '/search?q=test&udm=14&start=10&sei=x', '/search?q=C%2B%2B&udm=14&sei=normal50&start=50', '/search?q=test'):
                url = 'https://www.' + host + suffix
                self.assertTrue(any(fnmatch.fnmatchcase(url, pattern) for pattern in blocks), url)

    def test_firefox_duck_wildcard_has_only_narrow_noai_exceptions(self):
        policy = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']
        def matches(url, pattern):
            pattern = pattern.replace('?', '[?]')
            return fnmatch.fnmatchcase(url, pattern) or fnmatch.fnmatchcase(url, pattern.replace('://*.', '://', 1))
        for host in coach.helper.FIREFOX_DUCK_HOSTS + tuple(coach.helper.DUCK_ALIASES) + ('duck.ai', 'links.duckduckgo.com', 'random-cpcoach-927364.duckduckgo.com', 'nested.random.duckduckgo.com'):
            for suffix in ('/', '/?q=write+binary+search+in+python', '/?q=test&ia=chat', '/?q=test&ia=assist', '/?q=test&assist=true', '/chat', '/aichat', '/duckchat/v1/chat'):
                url = 'https://' + host + suffix
                self.assertTrue(any(matches(url, pattern) for pattern in policy['Block']), url)
                self.assertFalse(any(matches(url, pattern) for pattern in policy['Exceptions']), url)
        for suffix in ('/', '/?q=test', '/?q=test&ia=chat', '/?ia=chat&q=test', '/?ia=assist&q=test', '/chat', '/aichat', '/duckchat/v1/chat', '/duckchat/v1/status', '/duckchat/v1/auth/token', '/duckassist', '/assist', '/ask', '/chat?q=test', '/duckchat/v1/chat?q=test', '/html/duckchat/v1/chat?q=test', '/html/?ia=chat&q=test'):
            url = 'https://noai.duckduckgo.com' + suffix
            self.assertTrue(any(matches(url, pattern) for pattern in policy['Block']), url)
            self.assertFalse(any(matches(url, pattern) for pattern in policy['Exceptions']), url)
        for suffix in ('/html/', '/html/?q=write+binary+search+in+python', '/html/?q=C%2B%2B&ia=web', '/html/?q=binary+search&ia=web&s=10'):
            url = 'https://noai.duckduckgo.com' + suffix
            self.assertTrue(any(matches(url, pattern) for pattern in policy['Block']), url)
            self.assertTrue(any(matches(url, pattern) for pattern in policy['Exceptions']), url)
        self.assertEqual([p for p in policy['Exceptions'] if 'duck' in p],
                         ['https://noai.duckduckgo.com/html/', 'https://noai.duckduckgo.com/html/?q=*'])
        for suffix in ('/html/?q=test&ia=chat', '/html/?q=test&ia=assist', '/html/?q=test&assist=true', '/html/?q=test&chat=true', '/html/?q=test&ask=true', '/html/?q=test&ia=%63hat', '/html/?q=test&%69a=chat'):
            url = 'https://noai.duckduckgo.com' + suffix
            self.assertTrue(any(matches(url, pattern) for pattern in policy['Exceptions']), url)
        strict = coach.helper.browser_policy('firefox', allowed=['example.org'])['policies']
        self.assertEqual(strict['SearchEngines']['Default'], coach.helper.GOOGLE_WEB_NAME)
        self.assertEqual(strict['WebsiteFilter']['Block'], ['<all_urls>'])
        self.assertEqual(strict['WebsiteFilter']['Exceptions'], ['*://example.org/*', '*://localhost/*', '*://127.0.0.1/*', '*://[::1]/*'])

    def test_firefox_web_preserves_engines_and_prevents_installs(self):
        helper = coach.helper
        engines = helper.browser_policy('firefox')['policies']['SearchEngines']
        self.assertEqual(engines, {'PreventInstalls': True})
        strict = helper.browser_policy('firefox', allowed=['example.org'])['policies']['SearchEngines']
        self.assertNotIn('PreventInstalls', strict)
        self.assertNotIn('Remove', strict)

    def test_firefox_alternative_search_routes_are_blocked_without_exceptions(self):
        policy = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']
        for url in ('https://www.bing.com/search?q=test', 'https://www.bing.com/copilotsearch?q=test', 'https://search.brave.com/search?q=test', 'https://search.brave.com/ask?q=test', 'https://www.ecosia.org/search?q=test', 'https://www.ecosia.org/chat?q=test', 'https://search.yahoo.com/search?p=test', 'https://search.yahoo.co.jp/search?p=test', 'https://www.startpage.com/sp/search?query=test', 'https://www.qwant.com/?q=test', 'https://www.perplexity.ai/search?q=test', 'https://you.com/search?q=test', 'https://kagi.com/search?q=test', 'https://yandex.ru/search/?text=test', 'https://yandex.com.tr/search/?text=test', 'https://en.wikipedia.org/wiki/Special:Search?search=test', 'https://www.amazon.com/s?k=test', 'https://www.ebay.co.uk/sch/i.html?_nkw=test'):
            self.assertTrue(any(fnmatch.fnmatchcase(url, pattern) or fnmatch.fnmatchcase(url, pattern.replace('://*.', '://', 1)) for pattern in policy['Block']), url)
            self.assertFalse(any(fnmatch.fnmatchcase(url, pattern) for pattern in policy['Exceptions']), url)
        for url in ('https://www.mojeek.com/search?q=test', 'https://en.wikipedia.org/wiki/Binary_search_algorithm'):
            self.assertFalse(any(fnmatch.fnmatchcase(url, pattern) for pattern in policy['Block']), url)

    def test_keyless_sites_demo_wildcards_and_history(self):
        sites = ('heck.ai', 'chat.akash.network', 'chatbot.app', 'aichatting.net', 'unlimitedai.org', 'metaso.cn', 'exa.ai', 'morphic.sh', 'arena.ai', 'lmarena.ai', 'qwen-qwen3-demo.hf.space', 'random-demo.hf.space', 'random-demo.gradio.live')
        firefox = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']
        chrome = coach.helper.browser_policy('chrome')
        for host in sites:
            url = 'https://' + host + '/'
            self.assertTrue(coach.ai_url(url), url)
            self.assertTrue(any(fnmatch.fnmatchcase(url, p) or fnmatch.fnmatchcase(url, p.replace('://*.', '://', 1)) for p in firefox['Block']), url)
            self.assertFalse(any(fnmatch.fnmatchcase(url, p) for p in firefox['Exceptions']), url)
            self.assertTrue(any(host == p or host.endswith('.' + p) for p in chrome['URLBlocklist']), url)
        for url in ('', 'https://hf.space.example.org/', 'https://gradio.live.example.org/', 'https://noai.duckduckgo.com/html/?q=test'):
            self.assertFalse(coach.ai_url(url), url)

    def test_google_ai_mode_policies_and_history(self):
        chrome = coach.helper.browser_policy('chrome')
        self.assertEqual(chrome['AIModeSettings'], 1)
        self.assertEqual(chrome['GeminiSettings'], 1)
        for key in ('HelpMeWriteSettings', 'TabCompareSettings', 'HistorySearchSettings', 'DevToolsGenAiSettings', 'CreateThemesSettings'):
            self.assertEqual(chrome[key], 2)
        self.assertIn('google.com/?udm=50', chrome['URLBlocklist'])
        self.assertNotIn('google.com', chrome['URLBlocklist'])
        firefox = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']['Block']
        self.assertLessEqual(len(firefox), 1000)
        self.assertIn('*://*.google.com/*udm=*', firefox)
        for url in ('https://www.google.com/search?udm=50&q=binary', 'https://google.com/search?q=binary&udm=50&hl=en', 'https://www.google.pt/search?udm=%35%30', 'https://google.com/ai', 'https://google.com/async/folif'):
            self.assertTrue(coach.ai_url(url), url)
        for url in ('https://www.google.com/search?q=binary+search+codeforces', 'https://google.com/search?udm=14&q=test', 'https://google.com/search?udm=500', 'https://google.com/search?q=udm%3D50', 'https://google.com.evil.example/search?udm=50', ''):
            self.assertFalse(coach.ai_url(url), url)

    def test_fixed_policy_patterns_and_dns(self):
        chrome = coach.helper.browser_policy('chrome')
        self.assertEqual(chrome['DnsOverHttpsMode'], 'off')
        self.assertFalse(chrome['QuicAllowed'])
        self.assertIn('duckduckgo.com', chrome['URLBlocklist'])
        self.assertIn('platform.openai.com/playground', chrome['URLBlocklist'])
        self.assertNotIn('github.com', chrome['URLBlocklist'])
        firefox = coach.helper.browser_policy('firefox')['policies']
        self.assertEqual(firefox['DNSOverHTTPS'], {'Enabled': False, 'Locked': True})
        for domain in ('duck.ai', 'chat.qwen.ai'):
            self.assertIn('*://*.' + domain + '/*', firefox['WebsiteFilter']['Block'])
        self.assertIn('*://*.duckduckgo.com/*', firefox['WebsiteFilter']['Block'])
        self.assertIn('*://help.openai.com/*', firefox['WebsiteFilter']['Exceptions'])
        self.assertTrue(coach.ai_url('https://duckduckgo.com/?q=test&ia=chat'))
        self.assertTrue(coach.ai_url('https://duckduckgo.com/duckchat/v1/chat'))
        self.assertFalse(coach.ai_url('https://duckduckgo.com/?q=codeforces'))

    def test_duck_alias_network_policy_and_uninstall_coverage(self):
        helper = coach.helper
        chrome = helper.browser_policy('chrome')
        script = Path(__file__).with_name('uninstall.sh').read_text()
        namespace = {}
        exec('domains = ' + script.split('domains = ', 1)[1].split('\nentries = ', 1)[0], namespace)
        fallback = {f'{ip} {host}' for host in namespace['domains'] + ['www.' + h for h in namespace['domains']] for ip in ('0.0.0.0', '::')}
        self.assertTrue(helper.ENTRIES <= fallback, helper.ENTRIES - fallback)
        for host in helper.DUCK_ALIASES + helper.DUCK_SEARCH_HOSTS:
            for name in (host, 'www.' + host):
                self.assertIn('0.0.0.0 ' + name + '\n', helper.BLOCK)
                self.assertEqual(helper.stripped(helper.BEGIN + '\n0.0.0.0 ' + name + '\n' + helper.END + '\n'), '')
            self.assertIn(host, namespace['domains'])
            self.assertIn(host, chrome['URLBlocklist'])
        self.assertIn('duckduckgo.com', chrome['URLBlocklist'])
        self.assertFalse(any('duck' in p for p in chrome['URLAllowlist']))
        self.assertNotIn('0.0.0.0 noai.duckduckgo.com\n', helper.BLOCK)

    def test_firefox_policy_stays_within_native_pattern_limit(self):
        policy = coach.helper.browser_policy('firefox')['policies']['WebsiteFilter']
        self.assertLessEqual(len(policy['Block']), 1000)
        self.assertLessEqual(len(policy['Exceptions']), 1000)

    def test_exact_policy_restore_and_repeat_apply(self):
        helper = coach.helper
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            chrome, firefox = home / 'chrome.json', home / 'firefox.json'
            before = b'{ "policies": {"DisableTelemetry":true,"Preferences":{"test.pref":true}} }\n'
            firefox.write_bytes(before)
            firefox.chmod(0o640)
            with patch.object(helper, 'policy_targets', return_value=[(chrome, 'chrome'), (firefox, 'firefox')]), patch.object(helper, 'POLICY_STATE', home / 'state.json'), patch.object(helper, 'safe_path'), patch.object(helper.os, 'fchown'):
                helper.change_policies('apply')
                self.assertTrue(helper.policy_status())
                helper.change_policies('apply', ['example.org'])
                self.assertTrue(helper.policy_status(['example.org']))
                self.assertFalse(helper.policy_status())
                helper.change_policies('apply')
                self.assertTrue(json.loads(firefox.read_text())['policies']['DisableTelemetry'])
                helper.change_policies('remove')
                self.assertEqual(firefox.read_bytes(), before)
                self.assertEqual(firefox.stat().st_mode & 0o777, 0o640)
                self.assertFalse(chrome.exists())
                self.assertFalse((home / 'state.json').exists())
                helper.change_policies('remove')

    def test_invalid_policy_and_unsafe_target_not_overwritten(self):
        helper = coach.helper
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / 'policy.json'
            target.write_text('not json')
            with self.assertRaises(ValueError):
                helper.safe_path(target)
            with patch.object(helper, 'policy_targets', return_value=[(target, 'firefox')]), patch.object(helper, 'POLICY_STATE', home / 'state.json'), patch.object(helper, 'safe_path'):
                with self.assertRaises(ValueError):
                    helper.change_policies('apply')
            self.assertEqual(target.read_text(), 'not json')


class NativeWindowTests(unittest.TestCase):
    def test_close_warning_cancel_and_background_confirmation(self):
        from unittest.mock import MagicMock, Mock
        view = types.SimpleNamespace(create_window=MagicMock(), start=Mock())
        event = view.create_window.return_value.events.closing
        event.__iadd__.return_value = event
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)), patch.dict('sys.modules', {'webview': view, 'gi.repository': types.SimpleNamespace(GLib=Mock())}):
            coach.native_window('http://localhost:12345/?desktop=1')
        window = view.create_window.return_value
        handler = window.events.closing.__iadd__.call_args.args[0]
        def thread(target, **kwargs):
            return types.SimpleNamespace(start=target)
        def response(*args, **kwargs):
            return nullcontext(io.BytesIO(b'{"current":{"mode":"practice-no-ai"}}'))
        with patch.object(coach.threading, 'Thread', side_effect=thread), patch.object(coach, 'urlopen', side_effect=response):
            window.create_confirmation_dialog.return_value = False
            self.assertFalse(handler())
            window.destroy.assert_not_called()
            window.create_confirmation_dialog.return_value = True
            self.assertFalse(handler())
            window.destroy.assert_called_once()
            self.assertTrue(handler())

    def test_persistent_storage_configuration(self):
        from unittest.mock import MagicMock, Mock
        view = types.SimpleNamespace(create_window=MagicMock(), start=Mock())
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)), patch.dict('sys.modules', {'webview': view, 'gi.repository': types.SimpleNamespace(GLib=Mock())}), patch.object(coach.Handler, 'native_downloads', False):
            coach.native_window('http://localhost:12345/')
            storage = Path(temporary) / '.local/share/cp-coach/webview'
            self.assertTrue(storage.is_dir())
            view.start.assert_called_once_with(gui='gtk', private_mode=False, storage_path=str(storage), icon=str(coach.ROOT / 'cp-coach.png'))
            api = view.create_window.call_args.kwargs['js_api']
            self.assertIsInstance(api, coach.DesktopAPI)
            self.assertIs(api._window, view.create_window.return_value)
            self.assertTrue(coach.Handler.native_downloads)

    def test_download_save_empty_cancel_and_error(self):
        from unittest.mock import Mock
        api = coach.DesktopAPI()
        api._window = Mock()
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)), patch.dict('sys.modules', {'webview': types.SimpleNamespace(FileDialog=types.SimpleNamespace(SAVE=30))}):
            home = Path(temporary)
            destination = home / 'copy.json'
            api._window.create_file_dialog.return_value = (str(destination),)
            data = json.dumps({'cptools': 1, 'data': {}, 'text': 'cópia'}).encode()
            saved = api.save_download('../../cópia.json', base64.b64encode(data).decode())
            self.assertTrue(saved['saved'])
            self.assertEqual(destination.read_bytes(), data)
            api._window.create_file_dialog.assert_called_with(30, directory=str(home), save_filename='cópia.json')
            (home / 'Downloads').mkdir()
            self.assertTrue(api.save_download('', '')['saved'])
            self.assertEqual(destination.read_bytes(), b'')
            api._window.create_file_dialog.assert_called_with(30, directory=str(home / 'Downloads'), save_filename='download')
            api._window.create_file_dialog.return_value = None
            self.assertEqual(api.save_download('copy.json', ''), {'cancelled': True})
            for name, encoded in ((None, ''), ('copy.json', 'invalid base64!'), ('copy.json', None)):
                self.assertIn('error', api.save_download(name, encoded))
            api._window.create_file_dialog.return_value = (str(home),)
            self.assertIn('error', api.save_download('copy.json', ''))

    def test_download_bridge_is_native_only(self):
        from unittest.mock import Mock
        handler = coach.Handler.__new__(coach.Handler)
        handler.headers = {'Host': 'localhost'}
        handler.path = '/'
        handler.send = Mock()
        handler.native_downloads = False
        handler.do_GET()
        browser_page = handler.send.call_args.args[1]
        self.assertNotIn(b'__cpDownloads', browser_page)
        self.assertIn(b'cb-toggle', browser_page)
        startup = browser_page.index(b'async function start()')
        self.assertLess(browser_page.index(b'  show(tab);', startup), browser_page.index(b'await fetch("data"', startup))
        handler.native_downloads = True
        handler.do_GET()
        native_page = handler.send.call_args.args[1]
        self.assertIn(b'__cpDownloads', native_page)
        self.assertLess(native_page.index(b'__cpDownloads'), native_page.index(b'const DOCS'))


class StrictNetworkTests(unittest.TestCase):
    def test_allowed_validation_normalizes_and_rejects_non_hosts(self):
        self.assertEqual(coach.helper.validate_allowed(' EXAMPLE.COM\n*.example.org\nexample.com\n'), ['*.example.org', 'example.com'])
        for value in ([], '', None, ['a.com'] * 129, [True], ['https://a.com'], ['a.com/path'], ['a.com:443'], ['a.*.com'], ['*a.com'], ['127.0.0.1'], ['*.127.0.0.1'], ['0x7f.0.0.1'], ['::1'], ['x.com\ninclude /etc/passwd'], ['x.com;drop table'], ['-x.com'], ['x..com'], ['x.' + 'a' * 64], ['a.com', '']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                coach.helper.validate_allowed(value)

    def test_strict_policies_have_no_search_or_help_exemptions(self):
        helper = coach.helper
        allowed = ['example.org', '*.example.com']
        chrome = helper.browser_policy('chrome', allowed=allowed)
        self.assertEqual(chrome['URLBlocklist'], ['*'])
        self.assertIn('.example.org', chrome['URLAllowlist'])
        self.assertIn('example.com', chrome['URLAllowlist'])
        self.assertNotIn('google.com/search?udm=14', chrome['URLAllowlist'])
        firefox = helper.browser_policy('firefox', allowed=allowed)['policies']['WebsiteFilter']
        self.assertEqual(firefox['Block'], ['<all_urls>'])
        self.assertIn('*://localhost/*', firefox['Exceptions'])
        self.assertIn('*://*.example.com/*', firefox['Exceptions'])
        self.assertNotIn('*://help.openai.com/*', firefox['Exceptions'])

    def test_nft_rules_are_uid_scoped_and_do_not_accept_established(self):
        body = coach.helper.nft_rules(1234, ['93.184.216.34', '2606:2800:220:1:248:1893:25c8:1946'], True)
        self.assertTrue(body.startswith('delete table inet cp_coach\n'))
        self.assertIn('meta skuid != 1234 return', body)
        self.assertIn('type ipv4_addr', body)
        self.assertIn('type ipv6_addr', body)
        self.assertIn('ip daddr @allowed4 accept', body)
        self.assertIn('fc00::/7', body)
        self.assertIn('reject with icmpx type admin-prohibited', body)
        self.assertNotIn('established', body)
        self.assertNotIn('flush ruleset', body)
        with self.assertRaises(ValueError):
            coach.helper.nft_rules(1234, ['1.2.3.4; flush ruleset'])

    def test_caller_uid_comes_only_from_privilege_launcher(self):
        for key in ('SUDO_UID', 'PKEXEC_UID'):
            with patch.dict(os.environ, {key: '1001'}, clear=True):
                self.assertEqual(coach.helper.caller_uid(), 1001)
        for value in ('', '0', '-1', '4294967295', '1; drop table'):
            with patch.dict(os.environ, {'SUDO_UID': value}, clear=True), self.assertRaises(ValueError):
                coach.helper.caller_uid()

    def test_google_country_list_and_new_ai_history(self):
        helper = coach.helper
        self.assertEqual(len(helper.GOOGLE_SEARCH_DOMAINS), 187)
        chrome = helper.browser_policy('chrome')
        firefox = helper.browser_policy('firefox')['policies']['WebsiteFilter']
        for host in helper.GOOGLE_SEARCH_DOMAINS:
            self.assertIn(host + '/search', chrome['URLBlocklist'])
            self.assertIn(host + '/search?udm=14', chrome['URLAllowlist'])
            self.assertIn('*://*.' + host + '/search*', firefox['Block'])
            self.assertIn('*://*.' + host + '/*udm=*', firefox['Block'])
            self.assertFalse(any(host in pattern for pattern in firefox['Exceptions']))
        for url in ('https://deepai.org/chat', 'https://iask.ai/', 'https://andisearch.com/', 'https://komo.ai/', 'https://search.brave.com/ask?q=python', 'https://bing.com/copilotsearch?q=python', 'https://www.bing.com/chat', 'https://alice.yandex.ru/', 'https://ya.ru/ai', 'https://ya.ru/neuro', 'https://google.se/search?udm=50', 'https://google.co.kr/search?udm=50', 'https://google.com.af/search?udm=50'):
            self.assertTrue(coach.ai_url(url), url)
        self.assertFalse(coach.ai_url('https://bing.com/search?q=codeforces'))
        self.assertFalse(coach.ai_url('https://ya.ru/search/?text=codeforces'))


class MonitorTests(unittest.TestCase):
    def test_browser_activation_raises_existing_window_without_opening_another(self):
        with patch.object(coach.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as focus, patch.object(coach, 'browser_window') as launch:
            coach.activate_browser('http://localhost:12345/')
            focus.assert_called_once()
            launch.assert_not_called()

    def test_browser_activation_reopens_closed_browser_window(self):
        with patch.object(coach.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)), patch.object(coach.threading, 'Event'), patch.object(coach, 'browser_window') as launch:
            coach.activate_browser('http://localhost:12345/')
            launch.assert_called_once_with('http://localhost:12345/')

    def test_single_window_activation_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}):
            owner, other = coach.WindowInstance(), coach.WindowInstance()
            try:
                self.assertTrue(owner.acquire())
                from unittest.mock import Mock
                focus = Mock()
                owner.on_activate(focus)
                self.assertFalse(other.acquire())
                focus.assert_called_once_with()
                with patch.dict(os.environ, {'DESKTOP_STARTUP_ID': 'cp-coach_TIME1234', 'XDG_ACTIVATION_TOKEN': 'test-token'}):
                    self.assertTrue(other.notify())
                self.assertEqual(owner.activation, {'DESKTOP_STARTUP_ID': 'cp-coach_TIME1234', 'XDG_ACTIVATION_TOKEN': 'test-token'})
                self.assertTrue(owner.path.exists())
                other.close()
                self.assertTrue(owner.path.exists())
            finally:
                owner.close()
            self.assertFalse(owner.path.exists())
            replacement = coach.WindowInstance()
            try:
                self.assertTrue(replacement.acquire())
            finally:
                replacement.close()

    def test_stale_socket_and_early_activation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}):
            stale = coach.socket.socket(coach.socket.AF_UNIX, coach.socket.SOCK_STREAM)
            stale.bind(str(Path(directory) / 'cp-coach-window.sock'))
            stale.close()
            owner, other = coach.WindowInstance(), coach.WindowInstance()
            try:
                self.assertTrue(owner.acquire())
                self.assertFalse(other.acquire())
                from unittest.mock import Mock
                focus = Mock()
                owner.on_activate(focus)
                focus.assert_called_once_with()
            finally:
                other.close()
                owner.close()

    def test_insecure_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}):
            Path(directory).chmod(0o755)
            with self.assertRaises(RuntimeError):
                coach.WindowInstance()

    def test_unset_runtime_without_logind_uses_private_fallback(self):
        original_exists = Path.exists
        default = Path(f'/run/user/{os.getuid()}')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=Path(directory)), patch.object(Path, 'exists', lambda path: False if path == default else original_exists(path)):
            instance = coach.WindowInstance()
            try:
                self.assertEqual(instance.path.parent, Path(os.environ['XDG_RUNTIME_DIR']))
                self.assertEqual(instance.path.parent.stat().st_mode & 0o777, 0o700)
                self.assertTrue(instance.acquire())
            finally:
                instance.close()

    def test_idle_autostart_does_not_start_a_monitor(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(coach, 'ROOT', Path(directory)), patch.object(coach.sys, 'argv', ['cp-coach.py', '--resume-only']), patch.object(coach, 'ensure_monitor') as start:
            coach.main()
            start.assert_not_called()

    def test_reopen_reuses_existing_monitor(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            address = home / 'address.json'
            address.write_text('{"url":"http://localhost:12345/","pid":123}')
            with patch.object(coach, 'monitor_paths', return_value=(home / 'lock', address, home / 'log')), patch.object(coach, 'urlopen', return_value=nullcontext(io.BytesIO(b'{}'))), patch.object(coach.subprocess, 'Popen') as launch:
                self.assertEqual(coach.ensure_monitor(types.SimpleNamespace(port=0)), 'http://localhost:12345/')
                launch.assert_not_called()

    def test_monitor_lock_prevents_duplicate_servers(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            lock = home / 'lock'
            with lock.open('w') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(coach, 'monitor_paths', return_value=(lock, home / 'address', home / 'log')), patch.object(coach, 'serve_monitor') as serve:
                    coach.monitor(types.SimpleNamespace(port=0))
                    serve.assert_not_called()


class HostsRepairTests(unittest.TestCase):
    def test_noai_session_addresses_and_cleanup(self):
        helper = coach.helper
        records = [(None, None, None, None, (ip, 443)) for ip in ('1.1.1.1', '1.1.1.1', '2606:4700:4700::1111', '127.0.0.1')]
        with patch.object(helper.socket, 'getaddrinfo', return_value=records):
            block = helper.noai_hosts_block()
        self.assertEqual(block, helper.BEGIN + '\n1.1.1.1 noai.duckduckgo.com\n2606:4700:4700::1111 noai.duckduckgo.com\n' + helper.END + '\n')
        before = '127.0.0.1 localhost\n192.0.2.1 noai.duckduckgo.com\n'
        self.assertEqual(helper.stripped(before + helper.BLOCK + block), before)
        self.assertEqual(helper.stripped(helper.BLOCK + block + block), '')

    def test_noai_empty_private_and_dns_failure_are_rejected(self):
        helper = coach.helper
        for records in ([], [(None, None, None, None, ('127.0.0.1', 443))]):
            with patch.object(helper.socket, 'getaddrinfo', return_value=records), self.assertRaises(ValueError):
                helper.noai_hosts_block()
        with patch.object(helper.socket, 'getaddrinfo', side_effect=helper.socket.gaierror('DNS unavailable')), self.assertRaises(ValueError):
            helper.noai_hosts_block()

    def test_noai_uninstall_fallback_matches_helper_cleanup(self):
        script = Path(__file__).with_name('uninstall.sh').read_text()
        body = script.split('def noai_entry(line):\n', 1)[1].split('\nfd = ', 1)[0]
        namespace = {'ipaddress': coach.helper.ipaddress}
        exec('def noai_entry(line):\n' + body, namespace)
        for line in ('1.1.1.1 noai.duckduckgo.com', '::1 noai.duckduckgo.com', 'garbage noai.duckduckgo.com', '1.1.1.1 unrelated.example', '', '1.1.1.1 noai.duckduckgo.com comment'):
            self.assertEqual(namespace['noai_entry'](line), coach.helper.noai_entry(line))

    def test_damaged_block_preserves_unrelated_entries(self):
        text = '127.0.0.1 localhost\n# BEGIN cp-coach\n0.0.0.0 chatgpt.com\n:: api.anthropic.com\n192.0.2.1 preserve.example\n'
        self.assertEqual(coach.helper.stripped(text), '127.0.0.1 localhost\n192.0.2.1 preserve.example\n')

    def test_stray_end_and_duplicate_markers(self):
        self.assertEqual(coach.helper.stripped('# END cp-coach\n127.0.0.1 localhost\n'), '127.0.0.1 localhost\n')
        self.assertEqual(coach.helper.stripped('# BEGIN cp-coach\n' + coach.helper.BLOCK), '')

    def test_empty_and_intact_block(self):
        self.assertEqual(coach.helper.stripped(''), '')
        self.assertEqual(coach.helper.stripped('127.0.0.1 localhost\n' + coach.helper.BLOCK), '127.0.0.1 localhost\n')

    def test_chatbot_hosts_and_non_chat_exceptions(self):
        for host in ('chat.z.ai', 'z.ai', 'kimi.com', 'www.kimi.com', 'chat.qwen.ai', 'notebooklm.google.com', 'api.anthropic.com'):
            self.assertIn(f'0.0.0.0 {host}\n', coach.helper.BLOCK)
        for host in coach.helper.NON_CHAT_HOSTS | {'github.com', 'googleapis.com', 'codeforces.com'}:
            self.assertNotIn(f'0.0.0.0 {host}\n', coach.helper.BLOCK)
        for url in ('https://api-docs.deepseek.com/', 'https://help.openai.com/', 'https://auth.openai.com/', 'https://auth0.openai.com/', 'https://status.openai.com/', 'https://docs.anthropic.com/', 'https://support.anthropic.com/', 'https://status.anthropic.com/', 'https://docs.github.com/', 'https://openai.com/', 'https://anthropic.com/', 'https://platform.deepseek.com/docs'):
            self.assertFalse(coach.ai_url(url), url)
        for url in ('https://sub.chat.z.ai/', 'https://chat.openai.com/', 'https://console.anthropic.com/', 'https://platform.openai.com/playground', 'https://api.anthropic.com/v1/messages'):
            self.assertTrue(coach.ai_url(url), url)


if __name__ == '__main__':
    unittest.main(verbosity=2)
