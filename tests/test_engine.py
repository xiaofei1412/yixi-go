import io
import json
import unittest
from concurrent.futures import Future
from unittest.mock import patch
from katago_service import KataGoEngine, EngineError


class FakeProcess:
    def __init__(self, lines=()):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(''.join(json.dumps(line)+'\n' for line in lines))
    def poll(self):
        return None


class EngineTests(unittest.TestCase):
    def test_out_of_order_warnings_and_partial_reports(self):
        engine = KataGoEngine()
        engine.pending = {'a': Future(), 'b': Future()}
        proc = FakeProcess([{'id':'a','warning':'test'}, {'id':'a','isDuringSearch':True,'rootInfo':{}},
                            {'id':'b','rootInfo':{'scoreLead':2}}, {'id':'a','rootInfo':{'scoreLead':1}}])
        engine.proc = proc
        engine._read(proc)
        self.assertEqual(engine.pending['a'].result()['rootInfo']['scoreLead'], 1)
        self.assertEqual(engine.pending['b'].result()['rootInfo']['scoreLead'], 2)

    def test_exit_releases_pending_requests(self):
        engine = KataGoEngine()
        engine.pending = {'a':Future()}
        engine.proc = FakeProcess()
        engine._read(engine.proc)
        with self.assertRaises(EngineError):
            engine.pending['a'].result()

    def test_timeout_sends_terminate_and_cleans_pending(self):
        engine = KataGoEngine()
        engine.proc = FakeProcess()
        with patch.object(engine, '_start'):
            with self.assertRaisesRegex(EngineError, '超时'):
                engine.query({'moves':[]}, timeout=.001)
        requests = [json.loads(line) for line in engine.proc.stdin.getvalue().splitlines()]
        self.assertEqual(requests[1]['action'], 'terminate')
        self.assertEqual(requests[1]['terminateId'], requests[0]['id'])
        self.assertEqual(engine.pending, {})
        self.assertTrue(engine.slots.acquire(blocking=False))


if __name__ == '__main__':
    unittest.main()
