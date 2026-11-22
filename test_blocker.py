import importlib.util
import json
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
        self.network_patch = patch.object(coach.Blocker, 'network_command', return_value=True)
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.blocker = coach.Blocker()

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
        self.assertEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')

    def test_process_exit_and_ai_mode(self):
        with patch.object(coach, 'process_info') as inspect:
            self.blocker.start('practice-ai', 120)
            self.blocker.scan_processes()
            inspect.assert_not_called()
        self.blocker.start('practice-no-ai', 120)
        info = dict(pid=999999, start='1234', process='claude', cmdline='claude')
        self.blocker.pending_processes['test'] = (1000, info)
        self.blocker.violation('processo', 'test', **info)
        with patch.object(coach, 'process_info', return_value=None), patch.object(Path, 'glob', return_value=[]):
            self.blocker.scan_processes()
        self.assertEqual(self.blocker.state['current']['violations'][0]['action'], 'terminado')
        self.assertFalse(self.blocker.pending_processes)

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


class NativeWindowTests(unittest.TestCase):
    def test_persistent_storage_configuration(self):
        from unittest.mock import Mock
        view = types.SimpleNamespace(create_window=Mock(), start=Mock())
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)), patch.dict('sys.modules', {'webview': view}):
            coach.native_window('http://localhost:12345/')
            storage = Path(temporary) / '.local/share/cp-coach/webview'
            self.assertTrue(storage.is_dir())
            view.start.assert_called_once_with(gui='gtk', private_mode=False, storage_path=str(storage), icon=str(coach.ROOT / 'cp-coach.png'))


class HostsRepairTests(unittest.TestCase):
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
