"""Desktop paths, private database initialization and single-instance behavior."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

import desktop_launcher
import katago_service
import server

ROOT = Path(__file__).resolve().parents[1]


class DesktopTests(unittest.TestCase):
    def test_source_keeps_existing_data_location(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'frozen', False, create=True):
            paths = runpy.run_path(str(ROOT/'product_paths.py'))
        self.assertEqual(paths['DATA_DIR'], ROOT/'product_data')

    def test_frozen_resources_and_private_data_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'LOCALAPPDATA': folder}, clear=True), \
                 patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, 'executable', str(Path(folder)/'readonly/YixiGo.exe')):
                paths = runpy.run_path(str(ROOT/'product_paths.py'))
            self.assertEqual(paths['DATA_DIR'], Path(folder)/'YixiGo')
            self.assertEqual(paths['ENGINE_DIR'], Path(folder)/'readonly/katago_engine')

    def test_explicit_acceptance_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'YIXI_DATA_DIR': folder}):
                paths = runpy.run_path(str(ROOT/'product_paths.py'))
            self.assertEqual(paths['DATA_DIR'], Path(folder).resolve())

    def test_new_users_have_empty_independent_databases(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder)/'user-a/games.sqlite3'
            second = Path(folder)/'user-b/games.sqlite3'
            with patch.object(server, 'DB_PATH', first), server.database() as con:
                con.execute("INSERT INTO games(id,data) VALUES ('private','{}')")
            with patch.object(server, 'DB_PATH', second), server.database() as con:
                tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                self.assertEqual(len(tables), 6)
                for table in tables:
                    self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            with patch.object(server, 'DB_PATH', first), server.database() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM games').fetchone()[0], 1)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file locking')
    def test_lock_blocks_second_instance_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            first = desktop_launcher.InstanceLock(Path(folder))
            try:
                with self.assertRaisesRegex(RuntimeError, '已经在运行'):
                    desktop_launcher.InstanceLock(Path(folder))
            finally:
                first.close()
            second = desktop_launcher.InstanceLock(Path(folder))
            second.close()

    def test_only_reopens_loopback_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for url in ('https://example.com', 'file:///secret', 'http://127.0.0.1:bad'):
                (path/'instance.json').write_text(json.dumps({'url': url}))
                self.assertIsNone(desktop_launcher.existing_url(path))
            (path/'instance.json').write_text(json.dumps({'url': 'http://127.0.0.1:12345'}))
            self.assertEqual(desktop_launcher.existing_url(path), 'http://127.0.0.1:12345')

    def test_frozen_engine_tuning_uses_private_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            files = [directory/name for name in ('katago.exe', 'model.gz', 'config.cfg')]
            for file in files:
                file.touch()
            with patch.object(katago_service, 'FROZEN', True), \
                 patch.object(katago_service, 'DATA_DIR', directory/'private'), \
                 patch.object(katago_service, 'ENGINE_FILES', files), \
                 patch.object(katago_service.subprocess, 'Popen') as popen, \
                 patch.object(katago_service.threading, 'Thread'):
                katago_service.KataGoEngine()._start()
            self.assertEqual(popen.call_args.args[0][-2:], ['-override-config', 'homeDataDir=.'])
            self.assertEqual(popen.call_args.kwargs['cwd'], str(directory/'private/engine'))


if __name__ == '__main__':
    unittest.main()
