"""Local KataGo study workspace. Run: python -m uvicorn server:app."""
import base64
import binascii
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager, contextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from go_game import Game, action_from_coordinate, coordinate
from katago_service import KataGoEngine, EngineError
from study_stats import summarize_study, attempt_record
from study_compare import candidate_line, recorded_continuation, replay_line
from product_paths import RESOURCE_ROOT, DATA_DIR

ROOT = RESOURCE_ROOT
DB_PATH = DATA_DIR / "games.sqlite3"
db_lock = threading.RLock()
engine = KataGoEngine()


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP)")
    con.execute("CREATE TABLE IF NOT EXISTS analysis (game_id TEXT, node TEXT, hash TEXT, data TEXT, PRIMARY KEY(game_id,node))")
    con.execute("CREATE TABLE IF NOT EXISTS study_points (id TEXT PRIMARY KEY, data TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', streak INTEGER NOT NULL DEFAULT 0, due TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS study_attempts (id TEXT PRIMARY KEY, point_id TEXT NOT NULL, result TEXT, created TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS study_tags (point_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(point_id,tag))")
    con.execute("CREATE TABLE IF NOT EXISTS study_comparisons (attempt_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    try:
        with con:
            yield con
    finally:
        con.close()


def load_game(game_id):
    with database() as con:
        row = con.execute("SELECT data FROM games WHERE id=?", (game_id,)).fetchone()
    if not row:
        raise HTTPException(404, "棋谱不存在，请从棋谱库重新打开")
    return Game.restore(json.loads(row[0]))


def save_game(game_id, game):
    with database() as con:
        con.execute("INSERT INTO games(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=CURRENT_TIMESTAMP",
                    (game_id, json.dumps(game.dump(), ensure_ascii=False)))


def snapshot(game_id, game):
    return dict(game.snapshot(), id=game_id)


def mutation(game_id, revision, operation):
    with db_lock:
        game = load_game(game_id)
        if game.revision != revision:
            raise HTTPException(409, "棋局已更新，已为你重新同步，请重试")
        try:
            operation(game)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        game.revision += 1
        save_game(game_id, game)
        return snapshot(game_id, game)


@asynccontextmanager
async def lifespan(app):
    yield
    engine.close()


app = FastAPI(title="弈习 · KataGo 围棋研习", lifespan=lifespan)


@app.get("/")
def home():
    return FileResponse(ROOT / "index.html")


@app.get("/app.js")
def javascript():
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/style.css")
def stylesheet():
    return FileResponse(ROOT / "style.css", media_type="text/css")


class NewGame(BaseModel):
    size: Literal[9, 13, 19] = 19
    komi: float = Field(default=7.5, ge=-150, le=150, allow_inf_nan=False)
    human_color: Literal[1, -1] = 1
    mode: Literal["play", "review"] = "play"
    handicap: Literal[0, 2, 3, 4, 5, 6, 7, 8, 9] = 0

    @field_validator("komi")
    @classmethod
    def half_point(cls, value):
        if value*2 != int(value*2):
            raise ValueError("贴目需为整数或半整数")
        return value


class Revision(BaseModel):
    revision: int = Field(ge=0)


class Move(Revision):
    action: int = Field(ge=0, le=361)


class Navigate(Revision):
    node: str = Field(max_length=12000)


class Analyze(BaseModel):
    node: str | None = Field(default=None, max_length=12000)
    visits: int = Field(default=200, ge=10, le=4000)


class AI(Revision):
    visits: int = Field(default=200, ge=10, le=4000)


class ImportGame(BaseModel):
    content: str = Field(max_length=2800000)


class Score(Revision):
    dead: list[int] = Field(default_factory=list, max_length=361)
    confirm: bool = False


class Preview(BaseModel):
    node: str = Field(max_length=12000)
    moves: list[str] = Field(max_length=50)


@app.post("/api/games")
def create_game(req: NewGame):
    if req.handicap and req.mode == "review":
        raise HTTPException(400, "自由复盘请先不开让子；让子棋谱可通过 SGF 导入")
    if req.handicap and req.human_color != 1:
        raise HTTPException(400, "让子模式请执黑放置让子")
    game = Game(**req.model_dump())
    game_id = uuid.uuid4().hex
    with db_lock:
        save_game(game_id, game)
    return snapshot(game_id, game)


@app.get("/api/games")
def list_games():
    with db_lock, database() as con:
        rows = con.execute("SELECT id,data,updated FROM games ORDER BY updated DESC, rowid DESC LIMIT 100").fetchall()
    items = []
    for game_id, data, updated in rows:
        game = Game.restore(json.loads(data))
        root = game.sgf.get_root()
        items.append({"id": game_id, "size": game.size, "updated": updated+"Z", "mode": game.mode,
                      "title": f"{root.get('PB') if root.has_property('PB') else '黑方'} vs {root.get('PW') if root.has_property('PW') else '白方'}",
                      "result": root.get("RE") if root.has_property("RE") else "进行中"})
    return items


@app.get("/api/games/{game_id}")
def get_game(game_id: str):
    with db_lock:
        return snapshot(game_id, load_game(game_id))


@app.post("/api/import")
def import_game(req: ImportGame):
    try:
        content = base64.b64decode(req.content, validate=True)
        if len(content) > 2_000_000:
            raise ValueError("棋谱文件不能超过 2 MB")
        game = Game.import_sgf(content)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, f"无法导入：{exc}") from exc
    game_id = uuid.uuid4().hex
    with db_lock:
        save_game(game_id, game)
    return snapshot(game_id, game)


@app.get("/api/games/{game_id}/sgf")
def export_game(game_id: str):
    with db_lock:
        game = load_game(game_id)
    return Response(game.export_sgf(), media_type="application/x-go-sgf",
                    headers={"Content-Disposition": 'attachment; filename="game.sgf"'})


@app.post("/api/games/{game_id}/move")
def play_move(game_id: str, req: Move):
    def operation(game):
        if game.mode == "play" and not game.handicap_left and game.position()[0].player != game.human_color:
            raise ValueError("现在轮到 KataGo，请点击继续对弈")
        game.play(req.action)
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/ai")
def play_ai(game_id: str, req: AI):
    with db_lock:
        game = load_game(game_id)
        if game.revision != req.revision:
            raise HTTPException(409, "棋局已更新，请重试")
        if game.mode != "play" or game.status != "playing" or game.handicap_left or game.position()[0].player == game.human_color:
            raise HTTPException(400, "当前不需要 KataGo 落子")
        query = game.query(req.visits)
        query["overrideSettings"] = {"wideRootNoise": 0.0}
    try:
        result = engine.query(query)
        move = min(result["moveInfos"], key=lambda m: m["order"])["move"]
        action = action_from_coordinate(move, game.size)
    except (EngineError, ValueError, KeyError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return mutation(game_id, req.revision, lambda current: current.play(action))


@app.post("/api/games/{game_id}/navigate")
def navigate(game_id: str, req: Navigate):
    def operation(game):
        if game.mode != "review":
            raise ValueError("请先进入复盘")
        game.node(req.node)
        game.cursor = req.node
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/review")
def review(game_id: str, req: Revision):
    def operation(game):
        if game.handicap_left:
            raise ValueError("请先完成让子摆放")
        game.mode = "review"
        game.status, game.dead, game.score_result = "playing", [], None
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/undo")
def undo(game_id: str, req: Revision):
    def operation(game):
        if game.handicap_left:
            raise ValueError("让子摆放中请重新开局")
        if game.mode == "play" and game.status == "finished":
            raise ValueError("对局已结束，请进入复盘")
        if game.cursor == "root":
            raise ValueError("已在棋谱起点")
        game.cursor = game.cursor.rsplit("/", 1)[0]
        if game.mode == "play":
            if game.position()[0].player != game.human_color and game.cursor != "root":
                game.cursor = game.cursor.rsplit("/", 1)[0]
            game.status, game.dead, game.score_result = "playing", [], None
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/resign")
def resign(game_id: str, req: Revision):
    def operation(game):
        if game.mode != "play" or game.status == "finished" or game.handicap_left:
            raise ValueError("当前不能认输")
        game.sgf.get_root().set("RE", "W+R" if game.human_color == 1 else "B+R")
        game.status = "finished"
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/score")
def score(game_id: str, req: Score):
    def operation(game):
        if game.mode != "play" or game.status != "scoring":
            raise ValueError("双方连续停着后才可结算")
        game.dead = req.dead
        game.score_result = game.position()[0].score(req.dead, game.komi)
        if req.confirm:
            game.status = "finished"
            game.sgf.get_root().set("RE", game.score_result["result"])
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/resume")
def resume(game_id: str, req: Revision):
    def operation(game):
        if game.mode != "play" or game.status != "scoring":
            raise ValueError("仅能从待结算状态恢复对局")
        game.status, game.dead, game.score_result = "playing", [], None
    return mutation(game_id, req.revision, operation)


@app.post("/api/games/{game_id}/analysis")
def analyze(game_id: str, req: Analyze):
    with db_lock:
        game = load_game(game_id)
        if game.handicap_left:
            raise HTTPException(400, "请先完成让子摆放")
        path = req.node or game.cursor
        try:
            query = game.query(req.visits, path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        identity = {key: value for key, value in query.items() if key != "maxVisits"}
        identity["engine"] = engine.cache_identity
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with database() as con:
            cached = con.execute("SELECT data FROM analysis WHERE game_id=? AND node=? AND hash=?", (game_id, path, digest)).fetchone()
        if cached and json.loads(cached[0])["visits"] >= req.visits:
            return json.loads(cached[0])
    try:
        raw = engine.query(query)
    except EngineError as exc:
        raise HTTPException(503, str(exc)) from exc
    root = raw["rootInfo"]
    result = {"node": path, "black_winrate": root["winrate"], "black_lead": root["scoreLead"],
              "visits": root["visits"], "ownership": raw.get("ownership", []),
              "engine": engine.cache_identity,
              "candidates": sorted(raw["moveInfos"], key=lambda m: m["order"])[:5]}
    with db_lock, database() as con:
        previous = con.execute("SELECT data FROM analysis WHERE game_id=? AND node=? AND hash=?", (game_id, path, digest)).fetchone()
        if previous and json.loads(previous[0])["visits"] > result["visits"]:
            return json.loads(previous[0])
        con.execute("INSERT OR REPLACE INTO analysis VALUES (?,?,?,?)", (game_id, path, digest, json.dumps(result)))
    return result


@app.get("/api/games/{game_id}/analyses")
def analyses(game_id: str):
    with db_lock, database() as con:
        load_game(game_id)
        rows = con.execute("SELECT data FROM analysis WHERE game_id=?", (game_id,)).fetchall()
    return [item for row in rows if (item := json.loads(row[0])).get("engine") == json.loads(json.dumps(engine.cache_identity))]


@app.post("/api/games/{game_id}/preview")
def preview(game_id: str, req: Preview):
    with db_lock:
        game = load_game(game_id)
    try:
        board, _ = game.position(req.node)
        frames = []
        for move in req.moves:
            action = action_from_coordinate(move, game.size)
            board.play(action)
            frames.append({"board": [board.cells[i:i+game.size] for i in range(0, len(board.cells), game.size)],
                           "action": action, "player": board.player})
        return {"frames": frames}
    except ValueError as exc:
        raise HTTPException(400, f"变化无法完整回放：{exc}") from exc


# Study cards keep an immutable position, separate from the browsable game tree.
STUDY_VISITS = 400
STUDY_MIN_VISITS = 100
STUDY_TAGS = ('布局', '死活', '手筋', '对杀', '连接与切断', '劫争', '官子', '形势判断')


class StudyNode(Revision):
    node: str = Field(max_length=12000)


class StudyFilter(Revision):
    color: Literal[0, 1, -1] = 0


class StudyAnswer(BaseModel):
    action: int | None = Field(default=None, ge=0, le=361)


class StudyNote(BaseModel):
    note: str = Field(max_length=1000)


class StudyTags(BaseModel):
    tags: list[str] = Field(max_length=3)

    @field_validator('tags')
    @classmethod
    def known_tags(cls, values):
        if any(tag not in STUDY_TAGS for tag in values) or len(set(values)) != len(values):
            raise ValueError('请选择不重复的知识点标签，最多 3 个')
        return values


def study_now():
    return datetime.now(timezone.utc)


def study_query(query, move=None, player=1):
    payload = dict(query, maxVisits=STUDY_VISITS, includeOwnership=False,
                   overrideSettings={'wideRootNoise': 0})
    if move is not None:
        payload['allowMoves'] = [{'player': 'B' if player == 1 else 'W', 'moves': [move], 'untilDepth': 1}]
    try:
        raw = engine.query(payload)
    except EngineError as exc:
        raise HTTPException(503, str(exc)) from exc
    candidates = sorted(raw.get('moveInfos', []), key=lambda item: item['order'])
    if move is not None:
        candidates = [item for item in candidates if item['move'].lower() == move.lower()]
    if not candidates or candidates[0]['visits'] < STUDY_MIN_VISITS:
        raise HTTPException(422, '有效搜索不足，暂不能可靠判定；请稍后重试')
    return candidates[0]


def study_load(point_id, con):
    row = con.execute('SELECT data,note,streak,due FROM study_points WHERE id=?', (point_id,)).fetchone()
    if not row:
        raise HTTPException(404, '这道练习不存在')
    return dict(json.loads(row[0]), id=point_id, note=row[1], streak=row[2], due=row[3])


def study_public(point):
    return {key: point[key] for key in ('id', 'game_id', 'node', 'step', 'player', 'size', 'title', 'streak', 'due')}


@app.post('/api/games/{game_id}/study-shortlist')
def study_shortlist(game_id: str, req: StudyFilter):
    with db_lock:
        game = load_game(game_id)
        if game.revision != req.revision:
            raise HTTPException(409, '棋谱已更新，请重新筛选')
        if game.mode != 'review' or game.handicap_left:
            raise HTTPException(400, '请先进入复盘并完成让子摆放')
        route = game.snapshot()['route']
        cached = {item['node']: item for item in analyses(game_id)}
    candidates, pairs = [], 0
    for before, after in zip(route, route[1:]):
        color, move = game.node(after).get_move()
        if color is None:
            continue
        player = 1 if color == 'b' else -1
        if req.color and player != req.color:
            continue
        pairs += 1
        a, b = cached.get(before), cached.get(after)
        if a and b:
            swing = player * (a['black_lead'] - b['black_lead'])
            if swing >= 2:
                candidates.append({'node': after, 'swing': swing, 'index': route.index(after)})
    # Coarse swings are only a bounded shortlist, never saved as final move grades.
    candidates.sort(key=lambda item: item['swing'], reverse=True)
    selected = []
    for item in candidates:
        if all(abs(item['index'] - prior['index']) >= 3 for prior in selected):
            selected.append(item)
        if len(selected) == 6:
            break
    covered = sum(1 for a, b in zip(route, route[1:]) if a in cached and b in cached)
    return {'nodes': [item['node'] for item in selected], 'covered': covered,
            'total': max(0, len(route)-1), 'eligible': pairs}


@app.post('/api/games/{game_id}/study-points')
def study_create(game_id: str, req: StudyNode):
    with db_lock:
        game = load_game(game_id)
        if game.revision != req.revision:
            raise HTTPException(409, '棋谱已更新，请重新筛选')
        if game.mode != 'review' or game.handicap_left or req.node == 'root':
            raise HTTPException(400, '请在复盘中选择一手已落下的棋')
        try:
            node = game.node(req.node)
            if node.get_move()[0] is None:
                raise ValueError('该节点没有落子，请选择下一手棋')
            before = req.node.rsplit('/', 1)[0]
            board, moves = game.position(before)
            _, played = game.position(req.node)
            actual = played[-1][1]
            query = game.query(STUDY_VISITS, before)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        identity = {'game': game_id, 'node': req.node, 'query': query, 'actual': actual,
                    'engine': engine.cache_identity, 'version': 1}
        point_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with database() as con:
            if con.execute('SELECT 1 FROM study_points WHERE id=?', (point_id,)).fetchone():
                return {'saved': True, 'existing': True, 'point': study_public(study_load(point_id, con))}
    best = study_query(query)
    if actual.lower() == best['move'].lower():
        return {'saved': False, 'reason': '这手已是引擎推荐落点，无需收录为错题'}
    evaluated = study_query(query, actual, board.player)
    loss = max(0, board.player * (best['scoreLead'] - evaluated['scoreLead']))
    if loss < 2:
        return {'saved': False, 'reason': '复核后的预计损失不足 2 目，暂不收录'}
    frozen = game.dump()
    point = {'game_id': game_id, 'node': req.node, 'before': before, 'game': frozen,
             'query': query, 'actual': actual, 'best': best, 'actual_analysis': evaluated,
             'loss': loss, 'player': board.player, 'size': game.size, 'step': len(moves)+1,
             'title': f"{game.sgf.get_root().get('PB') if game.sgf.get_root().has_property('PB') else '黑方'} · {game.sgf.get_root().get('PW') if game.sgf.get_root().has_property('PW') else '白方'}",
             'engine': json.loads(json.dumps(engine.cache_identity))}
    with db_lock, database() as con:
        if load_game(game_id).revision != req.revision:
            raise HTTPException(409, '复核期间棋谱已更新，请重新筛选')
        con.execute('INSERT OR IGNORE INTO study_points(id,data,due) VALUES (?,?,?)',
                    (point_id, json.dumps(point, ensure_ascii=False), study_now().isoformat()))
        return {'saved': True, 'existing': False, 'point': study_public(study_load(point_id, con))}


@app.get('/api/study')
def study_list():
    now = study_now().isoformat()
    with db_lock, database() as con:
        rows = con.execute('SELECT id FROM study_points ORDER BY due,id').fetchall()
        tags = study_tag_map(con)
        points = []
        for row in rows:
            saved = study_load(row[0], con)
            item = study_public(saved)
            item['available'] = saved['engine'] == json.loads(json.dumps(engine.cache_identity))
            last = con.execute('SELECT result FROM study_attempts WHERE point_id=? AND result IS NOT NULL ORDER BY rowid DESC LIMIT 1', (row[0],)).fetchone()
            item['last_verdict'] = json.loads(last[0])['verdict'] if last else None
            item['tags'] = tags.get(row[0], [])
            points.append(item)
        completed = con.execute('SELECT COUNT(*) FROM study_attempts WHERE result IS NOT NULL').fetchone()[0]
    return {'points': points, 'due_count': sum(item['available'] and item['due'] <= now for item in points), 'completed': completed,
            'tag_options': STUDY_TAGS}


@app.post('/api/study/{point_id}/start')
def study_start(point_id: str):
    with db_lock, database() as con:
        point = study_load(point_id, con)
        if point['engine'] != json.loads(json.dumps(engine.cache_identity)):
            raise HTTPException(409, '引擎或模型已变化，请回原棋谱重新收录此局面')
        game = Game.restore(point['game'])
        board, _ = game.position(point['before'])
        attempt_id = uuid.uuid4().hex
        # An unfinished previous session cannot subsequently overwrite a newer review.
        con.execute('DELETE FROM study_attempts WHERE point_id=? AND result IS NULL', (point_id,))
        con.execute('INSERT INTO study_attempts VALUES (?,?,NULL,?)', (attempt_id, point_id, study_now().isoformat()))
    return {'attempt_id': attempt_id, 'point_id': point_id, 'size': game.size, 'komi': game.komi,
            'rules': game.rules, 'player': board.player, 'step': point['step'],
            'board': [board.cells[i:i+game.size] for i in range(0, len(board.cells), game.size)],
            'legal': board.legal_moves()}


@app.post('/api/study-attempts/{attempt_id}/answer')
def study_answer(attempt_id: str, req: StudyAnswer):
    with db_lock, database() as con:
        row = con.execute('SELECT point_id,result FROM study_attempts WHERE id=?', (attempt_id,)).fetchone()
        if not row:
            raise HTTPException(404, '本次练习已失效，请重新打开')
        if row[1]:
            return json.loads(row[1])
        point = study_load(row[0], con)
    if point['engine'] != json.loads(json.dumps(engine.cache_identity)):
        raise HTTPException(409, '引擎或模型已变化，请重新收录此局面')
    game = Game.restore(point['game'])
    board, _ = game.position(point['before'])
    loss, answer, evaluated = None, None, None
    if req.action is not None:
        try:
            board.play(req.action)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        answer = coordinate(req.action, game.size)
        if answer.lower() == point['best']['move'].lower():
            evaluated = point['best']
        elif answer.lower() == point['actual'].lower():
            evaluated = point['actual_analysis']
        else:
            evaluated = study_query(point['query'], answer, point['player'])
        loss = max(0, point['player'] * (point['best']['scoreLead'] - evaluated['scoreLead']))
    verdict = 'revealed' if answer is None else 'accepted' if loss <= 1 else 'retry'
    # Replay the recommendation using the same rules, including captures and ko.
    pv_board, _ = game.position(point['before'])
    frames = []
    for move in point['best'].get('pv', [point['best']['move']]):
        try:
            action = action_from_coordinate(move, game.size)
            pv_board.play(action)
        except ValueError:
            break
        frames.append({'board': [pv_board.cells[i:i+game.size] for i in range(0, len(pv_board.cells), game.size)], 'action': action})
    now = study_now()
    with db_lock, database() as con:
        existing = con.execute('SELECT result FROM study_attempts WHERE id=?', (attempt_id,)).fetchone()
        if not existing:
            raise HTTPException(409, '已打开新的练习，本次结果未写入，请使用最新练习')
        if existing[0]:
            return json.loads(existing[0])
        current = study_load(point['id'], con)
        streak, due = current['streak'], current['due']
        if verdict != 'accepted':
            streak, due = 0, (now + timedelta(minutes=10)).isoformat()
        elif due <= now.isoformat():
            streak += 1
            due = (now + timedelta(days=(1, 3, 7)[min(streak-1, 2)])).isoformat()
        result = {'verdict': verdict, 'answer': answer, 'loss': loss, 'actual': point['actual'],
                  'original_loss': point['loss'], 'best': point['best']['move'], 'frames': frames,
                  'reference_visits': point['best']['visits'], 'answer_visits': evaluated['visits'] if evaluated else None,
                  'due': due, 'streak': streak, 'note': current['note'], 'point_id': point['id'],
                  'source': {'game_id': point['game_id'], 'node': point['before']},
                  'completed_at': now.isoformat(),
                  'answer_analysis': {key: evaluated[key] for key in ('move', 'scoreLead', 'visits', 'pv') if key in evaluated} if evaluated else None}
        con.execute('UPDATE study_points SET streak=?,due=? WHERE id=?', (streak, due, point['id']))
        con.execute('UPDATE study_attempts SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), attempt_id))
    return result


@app.post('/api/study/{point_id}/note')
def study_note(point_id: str, req: StudyNote):
    with db_lock, database() as con:
        study_load(point_id, con)
        con.execute('UPDATE study_points SET note=? WHERE id=?', (req.note, point_id))
    return {'saved': True}


def study_tag_map(con):
    tags = {}
    for point_id, tag in con.execute('SELECT point_id,tag FROM study_tags ORDER BY tag'):
        tags.setdefault(point_id, []).append(tag)
    return tags


@app.post('/api/study/{point_id}/tags')
def study_save_tags(point_id: str, req: StudyTags):
    with db_lock, database() as con:
        study_load(point_id, con)
        con.execute('DELETE FROM study_tags WHERE point_id=?', (point_id,))
        con.executemany('INSERT INTO study_tags VALUES (?,?)', [(point_id, tag) for tag in req.tags])
    return {'tags': req.tags}


@app.get('/api/study/{point_id}/learning')
def study_learning(point_id: str):
    with db_lock, database() as con:
        point = study_load(point_id, con)
        tags = study_tag_map(con).get(point_id, [])
        rows = con.execute('SELECT id,point_id,result,created FROM study_attempts WHERE point_id=? AND result IS NOT NULL ORDER BY rowid DESC', (point_id,)).fetchall()
    history = sorted((attempt_record(row) for row in rows), key=lambda item: item['completed_at'], reverse=True)[:10]
    return {'tags': tags, 'tag_options': STUDY_TAGS, 'note': point['note'], 'history': history}


@app.get('/api/study/stats')
def study_statistics(days: Literal['7', '30', 'all'] = '7'):
    with db_lock, database() as con:
        rows = con.execute('SELECT id,point_id,result,created FROM study_attempts WHERE result IS NOT NULL ORDER BY rowid').fetchall()
        points = con.execute('SELECT id,data,due FROM study_points').fetchall()
        tags = study_tag_map(con)
    identity = json.loads(json.dumps(engine.cache_identity))
    available = {row[0] for row in points if json.loads(row[1])['engine'] == identity}
    now = study_now()
    return summarize_study(rows, tags, available, now, days,
                           sum(row[0] in available and datetime.fromisoformat(row[2]) <= now for row in points))


@app.post('/api/study-attempts/{attempt_id}/comparison')
def study_comparison(attempt_id: str):
    with db_lock, database() as con:
        row = con.execute('SELECT point_id,result FROM study_attempts WHERE id=?', (attempt_id,)).fetchone()
        if not row:
            raise HTTPException(404, '本次练习不存在或已失效')
        if not row[1]:
            raise HTTPException(409, '请先提交这一手或选择看答案，再打开变化对照')
        result = json.loads(row[1])
        point = study_load(row[0], con)
        if point['engine'] != json.loads(json.dumps(engine.cache_identity)):
            raise HTTPException(409, '引擎或模型已变化，请重新收录并完成练习后对照')
        cached = con.execute('SELECT data FROM study_comparisons WHERE attempt_id=?', (attempt_id,)).fetchone()
        if cached:
            return json.loads(cached[0])
    game = Game.restore(point['game'])
    board, _ = game.position(point['before'])
    lines = {'recorded': replay_line(board, recorded_continuation(game, point['node'])),
             'actual': candidate_line(board, point['actual_analysis']),
             'recommended': candidate_line(board, point['best']), 'answer': None}
    recomputed = False
    if result['answer'] is not None:
        evaluated = result.get('answer_analysis')
        if not evaluated:
            if result['answer'].lower() == point['best']['move'].lower():
                evaluated = point['best']
            elif result['answer'].lower() == point['actual'].lower():
                evaluated = point['actual_analysis']
            else:
                evaluated = study_query(point['query'], result['answer'], point['player'])
                recomputed = True
        lines['answer'] = candidate_line(board, evaluated)
    comparison = {'size': game.size, 'komi': game.komi, 'rules': game.rules,
                  'player': board.player, 'step': point['step'], 'lines': lines,
                  'original_loss': result['original_loss'], 'answer_loss': result['loss'],
                  'answer_recomputed': recomputed}
    with db_lock, database() as con:
        con.execute('INSERT OR IGNORE INTO study_comparisons VALUES (?,?)', (attempt_id, json.dumps(comparison, ensure_ascii=False)))
        return json.loads(con.execute('SELECT data FROM study_comparisons WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
