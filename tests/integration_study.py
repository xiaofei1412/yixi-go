"""Explicit Phase 2 real KataGo check; uses a temporary library only."""
import base64
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import server
from go_game import action_from_coordinate


def check(response):
    assert response.status_code == 200, response.text
    return response.json()


with tempfile.TemporaryDirectory() as directory:
    with patch.object(server, 'DB_PATH', Path(directory)/'study.sqlite3'), TestClient(server.app) as client:
        content = (server.ROOT/'examples/study-demo.sgf').read_bytes()
        game = check(client.post('/api/import', json={'content': base64.b64encode(content).decode()}))
        for node in game['route']:
            check(client.post(f"/api/games/{game['id']}/analysis", json={'node': node, 'visits': 100}))
        shortlist = check(client.post(f"/api/games/{game['id']}/study-shortlist", json={'revision': game['revision'], 'color': 1}))
        assert shortlist['nodes'] == ['root/0'], shortlist
        created = check(client.post(f"/api/games/{game['id']}/study-points", json={'node': 'root/0', 'revision': game['revision']}))
        assert created['saved'], created
        point = created['point']
        print('PASS real engine: scan, shortlist and verified card', flush=True)
        session = check(client.post(f"/api/study/{point['id']}/start", json={}))
        assert 'best' not in session and session['board'][4][4] == -1
        revealed = check(client.post(f"/api/study-attempts/{session['attempt_id']}/answer", json={'action': None}))
        assert revealed['verdict'] == 'revealed' and revealed['streak'] == 0
        assert revealed['frames'] and revealed['reference_visits'] >= server.STUDY_MIN_VISITS
        print(f"PASS reveal: reference {revealed['best']}, estimated loss {revealed['original_loss']:.2f}", flush=True)
        session = check(client.post(f"/api/study/{point['id']}/start", json={}))
        answer = action_from_coordinate(revealed['best'], session['size'])
        accepted = check(client.post(f"/api/study-attempts/{session['attempt_id']}/answer", json={'action': answer}))
        assert accepted['verdict'] == 'accepted' and accepted['loss'] == 0
        assert accepted['due'] == revealed['due'], 'Early review must not postpone due time'
        session = check(client.post(f"/api/study/{point['id']}/start", json={}))
        alternate = next(a for a in session['legal'] if a not in (answer, 0, session['size']**2))
        evaluated = check(client.post(f"/api/study-attempts/{session['attempt_id']}/answer", json={'action': alternate}))
        assert evaluated['answer_visits'] >= server.STUDY_MIN_VISITS
        assert evaluated['verdict'] in ('accepted', 'retry')
        check(client.post(f"/api/study/{point['id']}/note", json={'note': '先检查双方的气。'}))
        listing = check(client.get('/api/study'))
        assert len(listing['points']) == 1 and listing['completed'] == 3
        assert check(client.get(f"/api/games/{game['id']}")) == game
        print('PASS alternate root search, saved review schedule, notes and unchanged source', flush=True)
        check(client.post(f"/api/study/{point['id']}/tags", json={'tags':['死活','手筋']}))
        stats = check(client.get('/api/study/stats?days=7'))
        assert stats['summary']['completed'] == 3 and stats['summary']['answered'] == 2
        assert stats['summary']['revealed'] == 1 and stats['summary']['estimated_times'] == 0
        assert stats['summary']['pass_rate'] == (100 if evaluated['verdict'] == 'accepted' else 50)
        assert len(stats['topics']) == 2 and all(topic['sample_count'] == 1 and not topic['sufficient'] for topic in stats['topics'])
        learning = check(client.get(f"/api/study/{point['id']}/learning"))
        assert set(learning['tags']) == {'死活','手筋'} and len(learning['history']) == 3
        assert learning['note'] == '先检查双方的气。'
        assert not any(item['time_estimated'] for item in learning['history'])
        check(client.post(f"/api/study-attempts/{session['attempt_id']}/answer", json={'action': alternate}))
        assert check(client.get('/api/study/stats?days=all'))['summary']['completed'] == 3
        print('PASS learning center: real attempts, labels, rates, history and duplicate-submit stability', flush=True)
        before_comparison = check(client.get('/api/study/stats?days=all'))
        compared = check(client.post(f"/api/study-attempts/{session['attempt_id']}/comparison", json={}))
        assert compared['lines']['recorded']['moves'] == ['A9']
        assert compared['lines']['actual']['moves'][0] == 'A9'
        assert compared['lines']['recommended']['moves'][0] == revealed['best']
        assert compared['lines']['answer']['moves'][0] == evaluated['answer']
        for name, line in compared['lines'].items():
            assert 1 <= len(line['moves']) <= 12, (name, line)
            assert len(line['frames']) == len(line['moves'])+1
            assert '无法合法回放' not in line['warning'], (name, line['warning'])
        if revealed['best'] == 'G5':
            assert compared['lines']['recommended']['frames'][1]['captures']['black'] == 3
        assert not compared['answer_recomputed']
        assert check(client.post(f"/api/study-attempts/{session['attempt_id']}/comparison", json={})) == compared
        assert check(client.get('/api/study/stats?days=all')) == before_comparison
        assert check(client.get(f"/api/games/{game['id']}")) == game
        print('PASS comparison: recorded line, three real PVs, captures, cache and unchanged statistics', flush=True)
