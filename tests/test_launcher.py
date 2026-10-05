"""Exercise the real Windows launcher in disposable projects, without pip/network/server."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Windows batch launcher')
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.template = cls.base / 'template'
        venv.EnvBuilder(with_pip=False).create(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.project = self.base / ('围棋 project & space ' + self._testMethodName)
        self.project.mkdir()
        shutil.copyfile(ROOT/'start-product.cmd', self.project/'start-product.cmd')
        (self.project/'requirements-product.txt').write_text('', encoding='utf-8')
        # Local stand-ins let the batch script exercise startup without installing
        # packages or starting a listening service.
        for name in ('fastapi','sgfmill','httpx'):
            (self.project/f'{name}.py').write_text('', encoding='utf-8')
        (self.project/'uvicorn.py').write_text("from pathlib import Path\nPath('started.txt').write_text('ok')\n", encoding='utf-8')
        (self.project/'product_data').mkdir()
        (self.project/'product_data'/'keep.txt').write_text('saved games', encoding='utf-8')

    def launch(self):
        result = subprocess.run(['cmd.exe','/d','/c','start-product.cmd'], cwd=self.project,
                                input=b'\r\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        self.assertEqual(result.returncode,0,result.stdout.decode(errors='replace'))
        self.assertTrue((self.project/'started.txt').exists())
        self.assertEqual((self.project/'product_data'/'keep.txt').read_text(),'saved games')

    def test_healthy_environment_is_reused(self):
        shutil.copytree(self.template,self.project/'.venv')
        (self.project/'.venv'/'keep.txt').write_text('reuse')
        self.launch()
        self.assertTrue((self.project/'.venv'/'keep.txt').exists())
        self.assertEqual(list(self.project.glob('.venv.unusable-*')),[])

    def test_broken_copied_environment_is_preserved_and_rebuilt(self):
        shutil.copytree(self.template,self.project/'.venv')
        (self.project/'.venv'/'pyvenv.cfg').write_text('home = Z:\\missing-python-on-old-pc\nversion = 3.14.0\n', encoding='utf-8')
        self.launch()
        backups = list(self.project.glob('.venv.unusable-*'))
        self.assertEqual(len(backups),1)
        self.assertIn('missing-python-on-old-pc',(backups[0]/'pyvenv.cfg').read_text())
        self.assertNotIn('missing-python-on-old-pc',(self.project/'.venv'/'pyvenv.cfg').read_text(encoding='utf-8'))

    def test_first_launch_creates_environment(self):
        self.launch()
        self.assertTrue((self.project/'.venv'/'Scripts'/'python.exe').exists())
        self.assertEqual(list(self.project.glob('.venv.unusable-*')),[])

    def test_missing_system_python_reports_help_without_moving_old_environment(self):
        shutil.copytree(self.template,self.project/'.venv')
        (self.project/'.venv'/'pyvenv.cfg').write_text('home = Z:\\missing-python-on-old-pc\n', encoding='utf-8')
        environment = dict(os.environ, PATH=str(Path(os.environ['SystemRoot'])/'System32'))
        result = subprocess.run(['cmd.exe','/d','/c','start-product.cmd'],cwd=self.project,env=environment,
                                input=b'\r\n',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
        self.assertEqual(result.returncode,1)
        self.assertIn(b'No usable Python',result.stdout)
        self.assertTrue((self.project/'.venv'/'pyvenv.cfg').exists())
        self.assertEqual(list(self.project.glob('.venv.unusable-*')),[])
        self.assertFalse((self.project/'started.txt').exists())


if __name__ == '__main__':
    unittest.main()
