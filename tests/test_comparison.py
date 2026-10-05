"""Phase 4: immutable comparisons, exact replay and legacy repair."""
import json
import unittest
from unittest.mock import patch
import test_study as fixtures
import server
from go_game import Board, Game
from study_compare import replay_line, candidate_line, recorded_continuation
from katago_service import EngineError


class ReplayTests(unittest.TestCase):
    def test_capture_frames_and_source_board_unchanged(self):
        board = Board(9, [(1,1),(1,9),(1,19),(-1,10)])
        original = board.cells.copy()
        result = replay_line(board, ['C8','A1'])
        self.assertEqual(len(result['frames']),3)
        self.assertEqual(result['frames'][1]['board'][1][1],0)
        self.assertEqual(result['frames'][1]['captures'],{'black':1,'white':0})
        self.assertEqual(result['frames'][2]['captures'],{'black':1,'white':0})
        self.assertEqual(result['frames'][0]['captures'],{'black':0,'white':0})
        self.assertIsNone(result['frames'][0]['action'])
        self.assertEqual(board.cells, original)
        mirrored = Board(9, [(-1,1),(-1,9),(-1,19),(1,10)], player=-1)
        self.assertEqual(replay_line(mirrored,['C8'])['frames'][1]['captures'],{'black':0,'white':1})

    def test_pass_and_two_pass_stop(self):
        result = replay_line(Board(9), ['pass','pass','C7'])
        self.assertEqual(result['moves'],['pass','pass'])
        self.assertIn('连续停着',result['warning'])
        self.assertEqual(result['frames'][1]['action'],81)
        self.assertEqual(result['frames'][1]['player'],-1)

    def test_ko_illegal_tail_and_out_of_bounds_are_explicit(self):
        board = Board(5, [(1,1),(1,5),(1,11),(-1,6),(-1,2),(-1,8),(-1,12)])
        result = replay_line(board,['C4','B4'])
        self.assertEqual(result['moves'],['C4'])
        self.assertIn('劫',result['warning'])
        for moves in (['A9','A9'],['A9','T19']):
            result = replay_line(Board(9),moves)
            self.assertEqual(result['moves'],['A9'])
            self.assertIn('有效局面',result['warning'])

    def test_limit_missing_pv_and_inconsistent_first_move(self):
        result = replay_line(Board(19),[f'{col}19' for col in 'ABCDEFGHJKLMN'])
        self.assertEqual(len(result['moves']),12)
        self.assertIn('12',result['warning'])
        candidate = {'move':'C7','scoreLead':5,'visits':399}
        self.assertEqual(candidate_line(Board(9),candidate)['moves'],['C7'])
        candidate['pv'] = ['A9','B8']
        result = candidate_line(Board(9),candidate)
        self.assertEqual(result['moves'],['C7'])
        self.assertIn('首手不一致',result['warning'])

    def test_recorded_branch_selection_skips_comments(self):
        game = Game.import_sgf(b'(;SZ[9];B[aa](;W[bb])(;C[note];W[cc];B[dd]))')
        game.cursor = 'root/0/1/0'
        self.assertEqual(recorded_continuation(game,'root/0'),['A9','C7','D6'])
        self.assertEqual(recorded_continuation(game,'root/0/0'),['B8'])


class ComparisonTests(unittest.TestCase):
    setUp = fixtures.StudyTests.setUp
    tearDown = fixtures.StudyTests.tearDown
    check = fixtures.StudyTests.check
    game = fixtures.StudyTests.game
    create = fixtures.StudyTests.create
    start = fixtures.StudyTests.start
    answer = fixtures.StudyTests.answer

    def engine(self, payload):
        result = fixtures.StudyTests.engine(self,payload)
        move = result['moveInfos'][0]['move']
        result['moveInfos'][0]['pv'] = [move,'E5','F4']
        return result

    def comparison(self, session):
        return self.client.post(f"/api/study-attempts/{session['attempt_id']}/comparison",json={})

    def test_no_answers_before_submission_or_for_missing_session(self):
        point,_ = self.create()
        session = self.start(point)
        with patch.object(server.engine,'query',side_effect=AssertionError('must not search')):
            self.assertEqual(self.comparison(session).status_code,409)
            self.assertEqual(self.comparison({'attempt_id':'missing'}).status_code,404)
        self.assertEqual(self.check(self.client.get('/api/study'))['completed'],0)

    def test_new_answer_reuses_analysis_and_preserves_all_learning_state(self):
        point,game = self.create()
        session = self.start(point)
        with patch.object(server.engine,'query',side_effect=self.engine):
            answer = self.check(self.answer(session,30))
        before = self.check(self.client.get('/api/study'))
        with patch.object(server.engine,'query',side_effect=AssertionError('must not search')):
            comparison = self.check(self.comparison(session))
            self.assertEqual(self.check(self.comparison(session)),comparison)
        self.assertEqual(comparison['lines']['answer']['moves'],['D6','E5','F4'])
        self.assertEqual(comparison['lines']['recorded']['moves'],['A9','B9'])
        self.assertEqual(comparison['lines']['actual']['moves'],['A9','E5','F4'])
        self.assertEqual(comparison['lines']['recommended']['moves'],['C7','E5','F4'])
        self.assertFalse(comparison['answer_recomputed'])
        self.assertEqual(self.check(self.answer(session,0)),answer)
        self.assertEqual(self.check(self.client.get('/api/study')),before)
        self.assertEqual(self.check(self.client.get(f"/api/games/{game['id']}")),game)

    def test_reveal_has_no_fabricated_answer_and_white_metrics_are_black_view(self):
        point,_ = self.create(node='root/0/0')
        session = self.start(point)
        self.check(self.answer(session))
        data = self.check(self.comparison(session))
        self.assertIsNone(data['lines']['answer'])
        self.assertEqual(data['player'],-1)
        self.assertEqual(data['original_loss'],5)
        self.assertEqual(data['lines']['actual']['black_lead'],10)
        self.assertEqual(data['lines']['recommended']['black_lead'],5)
        self.assertEqual(data['lines']['actual']['frames'][1]['board'][0][1],-1)

    def make_legacy(self, session):
        with server.database() as con:
            raw = con.execute('SELECT result FROM study_attempts WHERE id=?',(session['attempt_id'],)).fetchone()[0]
            result = json.loads(raw)
            result.pop('answer_analysis',None)
            con.execute('UPDATE study_attempts SET result=? WHERE id=?',(json.dumps(result),session['attempt_id']))
        return result

    def test_legacy_alternative_recomputed_once_without_regrading(self):
        point,_ = self.create()
        session = self.start(point)
        with patch.object(server.engine,'query',side_effect=self.engine):
            self.check(self.answer(session,30))
        original = self.make_legacy(session)
        with patch.object(server.engine,'query',side_effect=self.engine) as query:
            first = self.check(self.comparison(session))
            self.assertEqual(self.check(self.comparison(session)),first)
            self.assertEqual(query.call_count,1)
            self.assertEqual(query.call_args.args[0]['allowMoves'][0]['moves'],['D6'])
        self.assertTrue(first['answer_recomputed'])
        self.assertEqual(self.check(self.answer(session)),original)
        self.assertEqual(self.check(self.client.get('/api/study'))['completed'],1)

    def test_failed_legacy_search_is_retryable_and_does_not_cache(self):
        point,_ = self.create()
        session = self.start(point)
        with patch.object(server.engine,'query',side_effect=self.engine):
            self.check(self.answer(session,30))
        original = self.make_legacy(session)
        with patch.object(server.engine,'query',side_effect=EngineError('offline')):
            self.assertEqual(self.comparison(session).status_code,503)
        with server.database() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM study_comparisons').fetchone()[0],0)
        with patch.object(server.engine,'query',side_effect=self.engine):
            self.assertTrue(self.check(self.comparison(session))['answer_recomputed'])
        self.assertEqual(self.check(self.answer(session)),original)

    def test_legacy_known_move_needs_no_search_and_model_change_blocks_cache(self):
        point,_ = self.create()
        session = self.start(point)
        self.check(self.answer(session,20))
        self.make_legacy(session)
        with patch.object(server.engine,'query',side_effect=AssertionError('must not search')):
            data = self.check(self.comparison(session))
        self.assertEqual(data['lines']['answer'],data['lines']['recommended'])
        with patch.object(server.engine,'cache_identity',[('changed-model',1,2)]):
            self.assertEqual(self.comparison(session).status_code,409)

    def test_source_navigation_after_collection_does_not_change_recorded_line(self):
        game = self.game(b'(;SZ[9];B[aa](;W[ba])(;W[cc];B[dd]))')
        game = self.check(self.client.post(f"/api/games/{game['id']}/navigate",json={'revision':game['revision'],'node':'root/0/1/0'}))
        point,game = self.create(game)
        self.check(self.client.post(f"/api/games/{game['id']}/navigate",json={'revision':game['revision'],'node':'root/0/0'}))
        session = self.start(point)
        self.check(self.answer(session))
        self.assertEqual(self.check(self.comparison(session))['lines']['recorded']['moves'],['A9','C7','D6'])


if __name__ == '__main__':
    unittest.main()
