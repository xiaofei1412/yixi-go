import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from katago_service import EngineError


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(server, 'DB_PATH', Path(self.temp.name) / 'study.sqlite3')
        self.db.start()
        self.client = TestClient(server.app)
        self.now = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
        self.clock = patch.object(server, 'study_now', return_value=self.now)
        self.clock.start()

    def tearDown(self):
        self.client.close()
        self.clock.stop()
        self.db.stop()
        self.temp.cleanup()

    def check(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def game(self, sgf=b'(;SZ[9]C[secret answer];B[aa];W[ba])'):
        return self.check(self.client.post('/api/import', json={'content': base64.b64encode(sgf).decode()}))

    def engine(self, payload):
        forced = payload.get('allowMoves')
        move = forced[0]['moves'][0] if forced else 'C7'
        score = {'C7': 5, 'A9': 0, 'B9': 10, 'D6': 4.5, 'pass': -10}.get(move, -3)
        return {'moveInfos': [{'move': move, 'scoreLead': score, 'visits': 399, 'order': 0, 'pv': [move], 'winrate': .6}]}

    def create(self, game=None, node='root/0'):
        game = game or self.game()
        with patch.object(server.engine, 'query', side_effect=self.engine):
            result = self.check(self.client.post(f"/api/games/{game['id']}/study-points", json={'node': node, 'revision': game['revision']}))
        self.assertTrue(result['saved'])
        return result['point'], game

    def start(self, point):
        return self.check(self.client.post(f"/api/study/{point['id']}/start", json={}))

    def answer(self, session, action=None):
        return self.client.post(f"/api/study-attempts/{session['attempt_id']}/answer", json={'action': action})

    def test_private_start_and_frozen_board_do_not_mutate_source(self):
        point, game = self.create()
        with server.database() as con:
            con.execute('UPDATE study_points SET note=?', ('secret note',))
        session = self.start(point)
        self.assertEqual(set(session), {'attempt_id', 'point_id', 'size', 'komi', 'rules', 'player', 'step', 'board', 'legal'})
        self.assertNotIn('secret', json.dumps(session))
        self.assertEqual(sum(map(sum, session['board'])), 0)
        result = self.check(self.answer(session, 20))
        self.assertEqual(result['note'], 'secret note')
        self.assertEqual(result['completed_at'], self.now.isoformat())
        self.assertEqual(result['verdict'], 'accepted')
        self.assertEqual(self.check(self.client.get(f"/api/games/{game['id']}")), game)
        self.assertEqual(result['frames'][0]['board'][2][2], 1)

    def test_black_and_white_loss_are_from_mover_perspective(self):
        for node, action, player in [('root/0', 0, 1), ('root/0/0', 1, -1)]:
            point, _ = self.create(node=node)
            self.assertEqual(point['player'], player)
            result = self.check(self.answer(self.start(point), action))
            self.assertEqual(result['original_loss'], 5)
            self.assertEqual((result['verdict'], result['loss']), ('retry', 5))

    def test_alternative_move_can_pass_with_forced_root_search(self):
        point, _ = self.create()
        session = self.start(point)
        with patch.object(server.engine, 'query', side_effect=self.engine) as engine:
            result = self.check(self.answer(session, 30))
        self.assertEqual((result['answer'], result['loss'], result['verdict']), ('D6', .5, 'accepted'))
        self.assertEqual(engine.call_args.args[0]['allowMoves'], [{'player': 'B', 'moves': ['D6'], 'untilDepth': 1}])
        self.assertEqual(engine.call_args.args[0]['overrideSettings'], {'wideRootNoise': 0})

    def test_idempotent_answers_and_duplicate_cards_preserve_schedule(self):
        point, game = self.create()
        session = self.start(point)
        result = self.check(self.answer(session, 20))
        self.assertEqual(self.check(self.answer(session, 0)), result)
        duplicate, _ = self.create(game)
        self.assertEqual(duplicate['id'], point['id'])
        self.assertEqual(duplicate['due'], result['due'])
        listing = self.check(self.client.get('/api/study'))
        self.assertEqual((len(listing['points']), listing['completed'], listing['due_count']), (1, 1, 0))

    def test_review_intervals_and_early_practice_do_not_farm_streak(self):
        point, _ = self.create()
        result = self.check(self.answer(self.start(point), 20))
        self.assertEqual(result['due'], (self.now+timedelta(days=1)).isoformat())
        early = self.check(self.answer(self.start(point), 20))
        self.assertEqual((early['streak'], early['due']), (1, result['due']))
        for days, expected_streak in [(3, 2), (7, 3), (7, 4)]:
            due = datetime.fromisoformat(result['due'])
            with patch.object(server, 'study_now', return_value=due):
                result = self.check(self.answer(self.start(point), 20))
            self.assertEqual(result['streak'], expected_streak)
            self.assertEqual(result['due'], (due+timedelta(days=days)).isoformat())

    def test_reveal_and_failure_reset_progress(self):
        point, _ = self.create()
        self.check(self.answer(self.start(point), 20))
        for action, verdict in [(None, 'revealed'), (0, 'retry')]:
            result = self.check(self.answer(self.start(point), action))
            self.assertEqual((result['verdict'], result['streak']), (verdict, 0))
            self.assertEqual(result['due'], (self.now+timedelta(minutes=10)).isoformat())

    def test_illegal_answer_and_engine_failure_leave_session_retryable(self):
        point, _ = self.create(node='root/0/0')
        session = self.start(point)
        self.assertEqual(self.answer(session, 0).status_code, 400)
        self.assertEqual(self.answer(session, 361).status_code, 400)
        with patch.object(server.engine, 'query', side_effect=EngineError('离线')):
            self.assertEqual(self.answer(session, 30).status_code, 503)
        self.assertEqual(self.check(self.client.get('/api/study'))['completed'], 0)
        self.assertEqual(self.check(self.answer(session, 20))['verdict'], 'accepted')

    def test_new_session_invalidates_unfinished_old_session(self):
        point, _ = self.create()
        old = self.start(point)
        new = self.start(point)
        self.assertEqual(self.answer(old, 20).status_code, 404)
        self.assertEqual(self.check(self.answer(new, 20))['verdict'], 'accepted')

    def test_no_card_for_small_loss_and_no_grade_for_insufficient_search(self):
        game = self.game(b'(;SZ[9];B[dd])')
        endpoint = f"/api/games/{game['id']}/study-points"
        with patch.object(server.engine, 'query', side_effect=self.engine):
            self.assertFalse(self.check(self.client.post(endpoint, json={'node': 'root/0', 'revision': 0}))['saved'])
        with patch.object(server.engine, 'query', return_value={'moveInfos': [{'order': 0, 'visits': 5}]}):
            self.assertEqual(self.client.post(endpoint, json={'node': 'root/0', 'revision': 0}).status_code, 422)
        self.assertEqual(self.check(self.client.get('/api/study'))['points'], [])
        point, _ = self.create()
        session = self.start(point)
        with patch.object(server.engine, 'query', return_value={'moveInfos': []}):
            self.assertEqual(self.answer(session, 30).status_code, 422)
        self.assertEqual(self.check(self.client.get('/api/study'))['completed'], 0)

    def test_engine_identity_guard_and_model_change_can_regenerate(self):
        point, game = self.create()
        session = self.start(point)
        with patch.object(server.engine, 'cache_identity', [('new-model', 1, 2)]):
            listing = self.check(self.client.get('/api/study'))
            self.assertFalse(listing['points'][0]['available'])
            self.assertEqual(listing['due_count'], 0)
            self.assertEqual(self.client.post(f"/api/study/{point['id']}/start", json={}).status_code, 409)
            self.assertEqual(self.answer(session, 20).status_code, 409)
            newer, _ = self.create(game)
            self.assertNotEqual(newer['id'], point['id'])

    def test_note_persistence_and_length_validation(self):
        point, _ = self.create()
        endpoint = f"/api/study/{point['id']}/note"
        self.check(self.client.post(endpoint, json={'note': '<script>不要漏看打吃</script>'}))
        self.assertEqual(self.client.post(endpoint, json={'note': 'x'*1001}).status_code, 422)
        result = self.check(self.answer(self.start(point)))
        self.assertEqual(result['note'], '<script>不要漏看打吃</script>')

    def test_stale_generation_rejected_before_and_after_search(self):
        game = self.game()
        endpoint = f"/api/games/{game['id']}/study-points"
        self.assertEqual(self.client.post(endpoint, json={'node': 'root/0', 'revision': 9}).status_code, 409)
        changed = False
        def query(payload):
            nonlocal changed
            if not changed:
                self.check(self.client.post(f"/api/games/{game['id']}/navigate", json={'node': 'root', 'revision': 0}))
                changed = True
            return self.engine(payload)
        with patch.object(server.engine, 'query', side_effect=query):
            self.assertEqual(self.client.post(endpoint, json={'node': 'root/0', 'revision': 0}).status_code, 409)
        self.assertEqual(self.check(self.client.get('/api/study'))['points'], [])

    def test_shortlist_uses_direction_color_spacing_and_cached_coverage(self):
        game = self.game(b'(;SZ[9];B[aa];W[ba];B[ca];W[da];B[ea];W[fa];B[ga])')
        # Black loses 5 at move 1; white GAINS 4 at move 2 (must not be flagged).
        scores = [5, 0, -4, -7, -2, -7, -2, -7]
        with server.database() as con:
            for path, score in zip(game['route'], scores):
                data = {'node': path, 'black_lead': score, 'engine': server.engine.cache_identity}
                con.execute('INSERT INTO analysis VALUES (?,?,?,?)', (game['id'], path, 'test', json.dumps(data)))
        endpoint = f"/api/games/{game['id']}/study-shortlist"
        result = self.check(self.client.post(endpoint, json={'revision': 0, 'color': -1}))
        self.assertNotIn('root/0/0', result['nodes'])
        self.assertEqual(result['nodes'], ['root/0/0/0/0'])
        self.assertEqual((result['covered'], result['total']), (7, 7))
        result = self.check(self.client.post(endpoint, json={'revision': 0, 'color': 1}))
        indexes = [game['route'].index(n) for n in result['nodes']]
        self.assertTrue(all(abs(a-b)>=3 for i,a in enumerate(indexes) for b in indexes[i+1:]))

    def test_pass_is_a_real_answer_not_a_reveal(self):
        point, _ = self.create()
        with patch.object(server.engine, 'query', side_effect=self.engine):
            result = self.check(self.answer(self.start(point), 81))
        self.assertEqual((result['answer'], result['verdict']), ('pass', 'retry'))

    def test_restart_reads_saved_cards_attempts_and_notes(self):
        point, _ = self.create()
        session = self.start(point)
        self.check(self.answer(session, 20))
        with TestClient(server.app) as another:
            data = self.check(another.get('/api/study'))
            self.assertEqual((data['completed'], data['points'][0]['streak']), (1, 1))
            self.assertEqual(data['points'][0]['last_verdict'], 'accepted')


if __name__ == '__main__':
    unittest.main()
