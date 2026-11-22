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
        with patch.object(self.blocker, 'network_command', side_effect=[False, True]) as command, patch.object(coach, 'history_databases', return_value=[]):
            self.blocker.poll()
            self.assertEqual(command.call_args_list[-1].args, ('apply',))
            self.assertEqual(self.blocker.state['current']['violations'][0]['kind'], 'bloqueio')
        self.blocker.state['current']['deadline'] = time.time() - 1
        with patch.object(coach, 'history_databases', return_value=[]):
            self.blocker.poll()
        self.assertIsNone(self.blocker.state['current'])

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
            view.start.assert_called_once_with(private_mode=False, storage_path=str(storage))


if __name__ == '__main__':
    unittest.main(verbosity=2)
