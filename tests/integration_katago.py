"""Explicit real-engine smoke test; does not write to the user's game library."""
import sys
from pathlib import Path
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import server


def check(response):
    assert response.status_code == 200, response.text
    return response.json()


with tempfile.TemporaryDirectory() as directory:
    with patch.object(server, 'DB_PATH', Path(directory) / 'smoke.sqlite3'), TestClient(server.app) as client:
        for size in (9, 13, 19):
            game = check(client.post('/api/games', json={'size': size, 'human_color': -1}))
            game = check(client.post(f"/api/games/{game['id']}/ai", json={'revision':game['revision'], 'visits':20}))
            assert game['step'] == 1 and game['player'] == -1
            result = check(client.post(f"/api/games/{game['id']}/analysis", json={'visits':20}))
            assert 0 <= result['black_winrate'] <= 1
            assert len(result['ownership']) == size*size
            pv = result['candidates'][0]['pv']
            preview = check(client.post(f"/api/games/{game['id']}/preview", json={'node':game['cursor'],'moves':pv}))
            assert len(preview['frames']) == len(pv)
            print(f"PASS {size}x{size}: AI move, white-to-play analysis, {len(pv)} PV frames", flush=True)
        game = check(client.post('/api/games', json={'size':9, 'handicap':2, 'komi':.5}))
        for action in (20,60):
            game = check(client.post(f"/api/games/{game['id']}/move", json={'revision':game['revision'],'action':action}))
        game = check(client.post(f"/api/games/{game['id']}/ai", json={'revision':game['revision'],'visits':20}))
        assert game['moves'][0][0] == 'W'
        print('PASS handicap: setup stones, explicit komi, white response', flush=True)
