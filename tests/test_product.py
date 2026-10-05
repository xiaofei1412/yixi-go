import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from go_game import Board, Game, action_from_coordinate
import server
from katago_service import EngineError


class RulesTests(unittest.TestCase):
    def test_capture_and_suicide(self):
        board = Board(3, [(1, 1), (1, 3), (1, 7), (-1, 4)])
        board.play(5)
        self.assertEqual(board.cells[4], 0)
        with self.assertRaisesRegex(ValueError, '自杀'):
            board.play(4)

    def test_occupied_bounds_and_turn_are_atomic(self):
        board = Board(9)
        board.play(0)
        for action, color in [(0, -1), (82, -1), (-1, -1), (1, 1)]:
            before = (board.cells.copy(), board.player, board.history.copy())
            with self.assertRaises(ValueError):
                board.play(action, color)
            self.assertEqual(before, (board.cells, board.player, board.history))

    def test_ko_and_simple_ko_after_passes(self):
        setup = [(1, 1), (1, 5), (1, 11), (-1, 6), (-1, 2), (-1, 8), (-1, 12)]
        for rule in ('Chinese', 'Japanese'):
            board = Board(5, setup, rules=rule)
            board.play(7)
            with self.assertRaisesRegex(ValueError, '劫'):
                board.play(6)
            board.play(25)
            board.play(25)
            if rule == 'Chinese':
                with self.assertRaisesRegex(ValueError, '劫'):
                    board.play(6)
            else:
                board.play(6)

    def test_two_passes_and_resume(self):
        game = Game(size=9)
        game.play(81)
        self.assertEqual(game.status, 'playing')
        game.play(81)
        self.assertEqual(game.status, 'scoring')
        with self.assertRaises(ValueError):
            game.play(0)
        game.status = 'playing'
        game.play(0)
        self.assertEqual(game.position()[0].passes, 0)

    def test_exact_area_score_and_dead_group(self):
        board = Board(3, [(1, 0), (1, 1), (1, 2), (1, 3), (1, 5), (1, 6), (1, 7), (-1, 4), (-1, 8)])
        score = board.score([4, 8], 0.5)
        self.assertEqual((score['black'], score['white'], score['result']), (9, 0.5, 'B+8.5'))
        self.assertEqual(board.cells[4], -1)
        neutral = Board(3, [(1, 0), (-1, 8)]).score([], 0.5)
        self.assertEqual((neutral['black'], neutral['white']), (1, 1.5))

    def test_handicap_is_setup_not_repeated_moves(self):
        game = Game(size=9, handicap=2, komi=0.5)
        game.play(20)
        with self.assertRaises(ValueError):
            game.play(81)
        game.play(60)
        self.assertEqual(game.position()[0].player, -1)
        self.assertEqual(game.query(10)['moves'], [])
        self.assertEqual(len(game.query(10)['initialStones']), 2)
        self.assertEqual(game.query(10)['komi'], 0.5)


class SgfTests(unittest.TestCase):
    def test_roundtrip_variations_comments_and_escaped_values(self):
        raw = '(;GM[1]FF[4]CA[UTF-8]SZ[9]KM[6.5]RU[Japanese]PB[张三]C[括号\\]与\\\\](;B[aa];W[bb])(;B[cc]C[变化]))'.encode()
        game = Game.import_sgf(raw)
        self.assertEqual(game.position()[1], [['B', 'A9'], ['W', 'B8']])
        game.cursor = 'root/1'
        game.play(30)
        restored = Game.restore(game.dump())
        self.assertEqual(restored.sgf.get_root().get('PB'), '张三')
        self.assertEqual(len(restored.sgf.get_root()), 2)
        self.assertEqual(restored.position()[1][-1], ['W', 'D6'])
        again = Game.import_sgf(restored.sgf.serialise())
        self.assertEqual(again.sgf.get_root().get('C'), '括号]与\\')

    def test_gbk_and_handicap_import(self):
        raw = '(;GM[1]FF[4]CA[GBK]SZ[9]KM[0.5]RU[Chinese]PB[棋友]HA[2]AB[cc][gg];W[ee])'.encode('gbk')
        game = Game.import_sgf(raw)
        self.assertEqual(Game.restore(game.dump()).sgf.get_root().get('PB'), '棋友')
        self.assertEqual(game.position()[0].player, 1)
        self.assertEqual(len(game.setup()), 2)

    def test_branch_does_not_destroy_original(self):
        game = Game.import_sgf(b'(;SZ[9];B[aa];W[bb])')
        game.cursor = 'root/0'
        game.play(20)
        self.assertEqual(len(game.node('root/0')), 2)
        self.assertEqual(game.position('root/0/0')[1][-1], ['W', 'B8'])

    def test_export_selected_line_as_main_preserves_original(self):
        game = Game.import_sgf(b'(;SZ[9];B[aa];W[bb])')
        game.cursor = 'root/0'
        game.play(20)
        exported = Game.import_sgf(game.export_sgf())
        self.assertEqual(exported.position()[1][-1], ['W', 'C7'])
        self.assertEqual(len(exported.node('root/0')), 2)
        self.assertEqual(game.cursor, 'root/0/1')

    def test_bad_import_rejected(self):
        for raw in (b'garbage', b'(;SZ[8])', b'(;SZ[9]RU[Unknown])',
                    b'(;SZ[9];B[aa];W[aa])', b'(;SZ[9];B[aa];AB[bb])',
                    b'(;SZ[9]KM[nan])', b'(;SZ[9];W[aa])', b'(;SZ[9])(;SZ[19])'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Game.import_sgf(raw)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, 'DB_PATH', Path(self.temp.name) / 'test.sqlite3')
        self.db_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp.cleanup()

    def new(self, **kwargs):
        response = self.client.post('/api/games', json={'size': 9, **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def change(self, game, endpoint, **kwargs):
        return self.client.post(f"/api/games/{game['id']}/{endpoint}", json={'revision': game['revision'], **kwargs})

    def test_independent_games_persistence_and_revision_guard(self):
        a, b = self.new(), self.new()
        moved = self.change(a, 'move', action=0).json()
        self.assertEqual(moved['step'], 1)
        self.assertEqual(self.client.get(f"/api/games/{b['id']}").json()['step'], 0)
        self.assertEqual(self.client.get(f"/api/games/{a['id']}").json()['board'][0][0], 1)
        self.assertEqual(self.change(a, 'move', action=1).status_code, 409)
        self.assertEqual(self.change(moved, 'move', action=1).status_code, 400)

    def test_invalid_input(self):
        for body in ({'size': 10}, {'komi': 7.2}, {'human_color': 0}):
            self.assertEqual(self.client.post('/api/games', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/import', json={'content':'!'}).status_code, 400)

    def test_sgf_import_export_and_navigation(self):
        response = self.client.post('/api/import', json={'content':base64.b64encode(b'(;SZ[9];B[aa];W[bb])').decode()})
        game = response.json()
        self.assertEqual(game['step'], 2)
        game = self.change(game, 'navigate', node='root').json()
        self.assertEqual(game['step'], 0)
        game = self.change(game, 'move', action=20).json()
        exported = self.client.get(f"/api/games/{game['id']}/sgf")
        self.assertEqual(len(Game.import_sgf(exported.content).sgf.get_root()), 2)
        self.assertEqual(self.change(game, 'navigate', node='root/-1').status_code, 400)

    def test_ai_chooses_order_and_keeps_black_perspective(self):
        game = self.new(human_color=-1)
        fake = {'rootInfo': {'winrate':.7, 'scoreLead':3.5, 'visits':10}, 'ownership':[.2]*81,
                'moveInfos':[{'order':1,'move':'B8','winrate':.6,'scoreLead':2,'visits':4,'pv':['B8']},
                             {'order':0,'move':'C7','winrate':.7,'scoreLead':3.5,'visits':6,'pv':['C7']}]}
        with patch.object(server.engine, 'query', return_value=fake):
            game = self.change(game, 'ai', visits=10).json()
            self.assertEqual(game['board'][2][2], 1)
            result = self.client.post(f"/api/games/{game['id']}/analysis", json={'visits':10}).json()
            self.assertEqual(result['black_winrate'], .7)
            self.assertEqual(result['black_lead'], 3.5)
            self.assertEqual(result['candidates'][0]['move'], 'C7')

    def test_engine_failure_does_not_advance_board(self):
        game = self.new(human_color=-1)
        with patch.object(server.engine, 'query', side_effect=EngineError('离线')):
            response = self.change(game, 'ai', visits=10)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get(f"/api/games/{game['id']}").json()['revision'], 0)

    def test_analysis_cache_reuses_deeper_result_and_invalidates_engine(self):
        game = self.new(mode='review')
        fake = {'rootInfo':{'winrate':.5,'scoreLead':0,'visits':400}, 'ownership':[0]*81, 'moveInfos':[]}
        endpoint = f"/api/games/{game['id']}/analysis"
        with patch.object(server.engine, 'query', return_value=fake) as query:
            self.assertEqual(self.client.post(endpoint, json={'visits':400}).status_code, 200)
            self.assertEqual(self.client.post(endpoint, json={'visits':100}).json()['visits'], 400)
            self.assertEqual(query.call_count, 1)
            with patch.object(server.engine, 'cache_identity', [('new-model',1,2)]):
                self.assertEqual(self.client.get(f"/api/games/{game['id']}/analyses").json(), [])
                self.assertEqual(self.client.post(endpoint, json={'visits':100}).status_code, 200)
                self.assertEqual(query.call_count, 2)

    def test_ai_stale_reply_cannot_overwrite_review(self):
        game = self.new(human_color=-1)
        def query(_):
            self.change(game, 'review')
            return {'moveInfos':[{'order':0,'move':'A9'}]}
        with patch.object(server.engine, 'query', side_effect=query):
            self.assertEqual(self.change(game, 'ai', visits=10).status_code, 409)
        current = self.client.get(f"/api/games/{game['id']}").json()
        self.assertEqual((current['mode'], current['step']), ('review', 0))

    def test_score_requires_two_passes_and_resign_preserves_record(self):
        game = self.new()
        self.assertEqual(self.change(game, 'score').status_code, 400)
        game = self.change(game, 'move', action=81).json()
        self.assertEqual(game['status'], 'playing')
        with patch.object(server.engine, 'query', return_value={'moveInfos':[{'order':0,'move':'pass'}]}):
            game = self.change(game, 'ai', visits=10).json()
        self.assertEqual(game['status'], 'scoring')
        game = self.change(game, 'score', dead=[], confirm=True).json()
        self.assertEqual((game['status'], game['result']), ('finished','W+7.5'))
        game = self.change(game, 'review').json()
        self.assertEqual((game['mode'], game['step']), ('review',2))

    def test_pv_simulates_capture_without_mutating_game(self):
        raw = b'(;SZ[9]AB[ba][ab][bc]AW[bb]PL[B])'
        game = self.client.post('/api/import', json={'content':base64.b64encode(raw).decode()}).json()
        result = self.client.post(f"/api/games/{game['id']}/preview",json={'node':'root','moves':['C8']}).json()
        self.assertEqual(result['frames'][0]['board'][1][1], 0)
        self.assertEqual(self.client.get(f"/api/games/{game['id']}").json()['board'][1][1], -1)


if __name__ == '__main__':
    unittest.main()
