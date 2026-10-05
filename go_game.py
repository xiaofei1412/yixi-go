"""Product game rules and SGF tree. Independent of the RL training environment."""
import math
from copy import copy
from sgfmill import sgf, sgf_grammar

COLS = "ABCDEFGHJKLMNOPQRST"
RULES = {
    "Chinese": {"ko": "POSITIONAL", "scoring": "AREA", "tax": "NONE", "hasButton": False,
                "suicide": False, "whiteHandicapBonus": "0", "friendlyPassOk": True},
    "Japanese": "japanese",
    "Korean": "korean",
}


def coordinate(action, size):
    return "pass" if action == size * size else f"{COLS[action % size]}{size - action // size}"


def action_from_coordinate(move, size):
    if move.lower() == "pass":
        return size * size
    try:
        col, row = COLS.index(move[0].upper()), size - int(move[1:])
    except (ValueError, IndexError):
        raise ValueError("引擎返回了无法识别的落点")
    if not (0 <= row < size and 0 <= col < size):
        raise ValueError("落点超出棋盘")
    return row * size + col


class Board:
    def __init__(self, size, setup=(), player=1, rules="Chinese"):
        self.size, self.player, self.rules = size, player, rules
        self.cells = [0] * (size * size)
        for color, action in setup:
            if not 0 <= action < len(self.cells) or self.cells[action]:
                raise ValueError("初始摆子重叠或超出棋盘")
            self.cells[action] = color
        self.history = [tuple(self.cells)]
        self.passes = 0

    def neighbors(self, point):
        r, c = divmod(point, self.size)
        for nr, nc in ((r-1, c), (r+1, c), (r, c-1), (r, c+1)):
            if 0 <= nr < self.size and 0 <= nc < self.size:
                yield nr * self.size + nc

    def group(self, point, cells=None):
        cells = self.cells if cells is None else cells
        color, group, liberties, pending = cells[point], {point}, set(), [point]
        while pending:
            for other in self.neighbors(pending.pop()):
                if cells[other] == 0:
                    liberties.add(other)
                elif cells[other] == color and other not in group:
                    group.add(other)
                    pending.append(other)
        return group, liberties

    def play(self, action, color=None):
        color = self.player if color is None else color
        if color != self.player:
            raise ValueError("落子颜色与当前轮次不符")
        if not 0 <= action <= len(self.cells):
            raise ValueError("落点超出棋盘")
        if action == len(self.cells):
            self.passes += 1
            self.history.append(tuple(self.cells))
            self.player = -color
            return
        if self.cells[action]:
            raise ValueError("该位置已有棋子")
        cells = self.cells.copy()
        cells[action] = color
        for other in self.neighbors(action):
            if cells[other] == -color:
                group, liberties = self.group(other, cells)
                if not liberties:
                    for stone in group:
                        cells[stone] = 0
        if not self.group(action, cells)[1]:
            raise ValueError("此处为自杀禁着点")
        position = tuple(cells)
        if self.rules == "Chinese":
            repeats = position in self.history
        else:
            repeats = len(self.history) >= 2 and position == self.history[-2]
        if repeats:
            raise ValueError("此处违反劫／重复局面规则，请先在别处落子")
        self.cells, self.player, self.passes = cells, -color, 0
        self.history.append(position)

    def legal_moves(self):
        result = []
        for action, stone in enumerate(self.cells):
            if stone:
                continue
            candidate = self.copy()
            try:
                candidate.play(action)
                result.append(action)
            except ValueError:
                pass
        return result + [len(self.cells)]

    def copy(self):
        clone = copy(self)
        clone.cells = self.cells.copy()
        # Historical positions are immutable tuples; don't copy all their cells per trial.
        clone.history = self.history.copy()
        return clone

    def score(self, dead, komi):
        if self.rules != "Chinese":
            raise ValueError("本阶段仅支持中国数子结算；此棋谱仍可使用 KataGo 分析")
        cells = self.cells.copy()
        for point in dead:
            if not 0 <= point < len(cells) or not self.cells[point]:
                raise ValueError("死子标记必须位于棋子上")
            for stone in self.group(point)[0]:
                cells[stone] = 0
        ownership, seen = cells.copy(), set()
        for point, stone in enumerate(cells):
            if stone or point in seen:
                continue
            region, borders, pending = {point}, set(), [point]
            seen.add(point)
            while pending:
                for other in self.neighbors(pending.pop()):
                    if cells[other]:
                        borders.add(cells[other])
                    elif other not in seen:
                        seen.add(other)
                        region.add(other)
                        pending.append(other)
            owner = next(iter(borders)) if len(borders) == 1 else 0
            for other in region:
                ownership[other] = owner
        black, white = ownership.count(1), ownership.count(-1) + komi
        difference = black - white
        result = "0" if difference == 0 else f"{'B' if difference > 0 else 'W'}+{abs(difference):g}"
        return {"black": black, "white": white, "komi": komi,
                "territory": ownership, "result": result}


class Game:
    def __init__(self, size=19, komi=7.5, human_color=1, mode="play", handicap=0):
        self.sgf = sgf.Sgf_game(size=size)
        root = self.sgf.get_root()
        for key, value in (("KM", komi), ("RU", "Chinese"), ("AP", ("GoStudy", "1")),
                           ("PB", "棋友" if human_color == 1 else "KataGo"),
                           ("PW", "棋友" if human_color == -1 else "KataGo")):
            root.set(key, value)
        self.sgf.set_date()
        if mode == "review":
            root.set("PB", "黑方")
            root.set("PW", "白方")
        if handicap:
            root.set("HA", handicap)
            root.set("PL", "w")
        self.cursor = "root"
        self.mode, self.human_color = mode, human_color
        self.handicap_left = handicap
        self.status, self.revision, self.dead = "playing", 0, []
        self.score_result = None

    @property
    def size(self):
        return self.sgf.get_size()

    @property
    def komi(self):
        return self.sgf.get_komi()

    @property
    def rules(self):
        return self.sgf.get_root().get("RU")

    def node(self, path=None):
        node = self.sgf.get_root()
        path = self.cursor if path is None else path
        try:
            parts = path.split("/")
            if parts[0] != "root":
                raise ValueError()
            for index in parts[1:]:
                if not index.isdigit():
                    raise ValueError()
                node = node[int(index)]
        except (ValueError, IndexError):
            raise ValueError("棋谱节点不存在")
        return node

    def ancestors(self, path=None):
        path = self.cursor if path is None else path
        parts = path.split("/")
        return [self.node("/".join(parts[:i])) for i in range(1, len(parts)+1)]

    def setup(self):
        root = self.sgf.get_root()
        black, white, _ = root.get_setup_stones()
        return [(color, (self.size-1-r)*self.size+c)
                for color, points in ((1, black), (-1, white)) for r, c in points]

    def initial_player(self):
        root = self.sgf.get_root()
        if root.has_property("PL"):
            return 1 if root.get("PL") == "b" else -1
        return -1 if (self.sgf.get_handicap() or 0) > 1 else 1

    def position(self, path=None):
        board = Board(self.size, self.setup(), self.initial_player(), self.rules)
        moves = []
        for node in self.ancestors(path):
            color, point = node.get_move()
            if color is None:
                continue
            action = self.size*self.size if point is None else (self.size-1-point[0])*self.size+point[1]
            board.play(action, 1 if color == "b" else -1)
            moves.append([color.upper(), coordinate(action, self.size)])
        return board, moves

    def play(self, action):
        if self.handicap_left:
            if action >= self.size*self.size or action < 0:
                raise ValueError("请在空交叉点放置让子")
            root = self.sgf.get_root()
            black, white, empty = root.get_setup_stones()
            point = (self.size-1-action//self.size, action % self.size)
            if point in black or point in white:
                raise ValueError("该位置已有棋子")
            black.add(point)
            root.set_setup_stones(black, white, empty)
            self.handicap_left -= 1
            return
        board, _ = self.position()
        if self.mode == "play" and self.status != "playing":
            raise ValueError("本局已进入结算或结束，请先继续对局或进入复盘")
        player = board.player
        board.play(action)
        point = None if action == self.size*self.size else (self.size-1-action//self.size, action % self.size)
        node = self.node()
        for i, child in enumerate(node):
            if child.get_move() == ("b" if player == 1 else "w", point):
                self.cursor += f"/{i}"
                break
        else:
            child = node.new_child()
            child.set_move("b" if player == 1 else "w", point)
            self.cursor += f"/{len(node)-1}"
        if self.mode == "play" and board.passes >= 2:
            self.status = "scoring"
        self.dead, self.score_result = [], None

    def query(self, visits, path=None):
        _, moves = self.position(path)
        return {"moves": moves, "initialStones": [["B" if c == 1 else "W", coordinate(a, self.size)] for c, a in self.setup()],
                "initialPlayer": "B" if self.initial_player() == 1 else "W", "rules": RULES[self.rules],
                "komi": self.komi, "boardXSize": self.size, "boardYSize": self.size,
                "maxVisits": visits, "includeOwnership": True, "analysisPVLen": 12}

    def snapshot(self):
        board, moves = self.position()
        node = self.node()
        root = self.sgf.get_root()
        children = []
        for i, child in enumerate(node):
            color, point = child.get_move()
            action = self.size*self.size if point is None else (self.size-1-point[0])*self.size+point[1]
            children.append({"id": self.cursor+f"/{i}", "label": "注释" if color is None else ("黑 " if color == "b" else "白 ")+coordinate(action, self.size)})
        route = ["root"]
        for part in self.cursor.split("/")[1:]:
            route.append(route[-1]+"/"+part)
        while len(self.node(route[-1])):
            route.append(route[-1]+"/0")
        return {"size": self.size, "komi": self.komi, "rules": self.rules,
                "board": [board.cells[i:i+self.size] for i in range(0, len(board.cells), self.size)],
                "player": board.player, "passes": board.passes, "moves": moves,
                "cursor": self.cursor, "parent": self.cursor.rsplit("/", 1)[0] if "/" in self.cursor else None,
                "children": children, "route": route, "step": len(moves), "revision": self.revision,
                "mode": self.mode, "human_color": self.human_color, "status": self.status,
                "handicap_left": self.handicap_left, "dead": self.dead, "score": self.score_result,
                "legal": board.legal_moves() if not self.handicap_left else [i for i, c in enumerate(board.cells) if not c],
                "comment": node.get("C") if node.has_property("C") else "",
                "black_name": root.get("PB") if root.has_property("PB") else "黑方",
                "white_name": root.get("PW") if root.has_property("PW") else "白方",
                "result": root.get("RE") if root.has_property("RE") else ""}

    def dump(self):
        return {"sgf": self.sgf.serialise().decode(self.sgf.get_charset()),
                "cursor": self.cursor, "mode": self.mode, "human_color": self.human_color,
                "handicap_left": self.handicap_left, "status": self.status,
                "revision": self.revision, "dead": self.dead, "score_result": self.score_result}

    def export_sgf(self):
        # Export the selected line as the main line without changing stable UI paths.
        exported = Game.restore(self.dump())
        selected = exported.ancestors()
        for node in selected[1:]:
            node.reparent(node.parent, index=0)
        return exported.sgf.serialise()

    @classmethod
    def restore(cls, data):
        game = cls()
        game.sgf = sgf.Sgf_game.from_string(data["sgf"])
        for key in ("cursor", "mode", "human_color", "handicap_left", "status", "revision", "dead", "score_result"):
            setattr(game, key, data[key])
        return game

    @classmethod
    def import_sgf(cls, content):
        game = cls(mode="review")
        try:
            if len(sgf_grammar.parse_sgf_collection(content)) != 1:
                raise ValueError("一个文件包含多盘棋，请拆分为单盘 SGF 后导入")
            game.sgf = sgf.Sgf_game.from_bytes(content)
            root = game.sgf.get_root()
            if game.size not in (9, 13, 19):
                raise ValueError("支持 9、13、19 路棋谱")
            if root.has_property("GM") and root.get("GM") != 1:
                raise ValueError("请导入围棋棋谱")
            rules = root.get("RU").lower() if root.has_property("RU") else "chinese"
            mapping = {"chinese": "Chinese", "china": "Chinese", "cn": "Chinese", "中国规则": "Chinese",
                       "japanese": "Japanese", "japan": "Japanese", "jp": "Japanese", "korean": "Korean"}
            if rules not in mapping:
                raise ValueError(f"暂不支持棋谱规则：{rules}")
            root.set("RU", mapping[rules])
            komi = game.komi if root.has_property("KM") else (6.5 if game.rules != "Chinese" else 7.5)
            if not math.isfinite(komi) or abs(komi) > 150 or komi*2 != int(komi*2):
                raise ValueError("贴目必须为 -150 到 150 之间的整数或半整数")
            root.set("KM", komi)
            base = Board(game.size, game.setup(), game.initial_player(), game.rules)
            pending, count = [(root, base, True)], 0
            while pending:
                node, board, is_root = pending.pop()
                count += 1
                if count > 3000:
                    raise ValueError("棋谱超过 3000 个节点，请拆分后导入")
                if not is_root and (node.has_setup_stones() or node.has_property("PL")):
                    raise ValueError("暂不支持中途编辑棋子或更换执棋方的棋谱，请导出正常对局分支")
                color, point = node.get_move()
                if color:
                    action = game.size*game.size if point is None else (game.size-1-point[0])*game.size+point[1]
                    board.play(action, 1 if color == "b" else -1)
                pending.extend((child, board.copy(), False) for child in node)
            while len(game.node()):
                game.cursor += "/0"
        except (RecursionError, LookupError, UnicodeError) as exc:
            raise ValueError("棋谱编码或变化树无法读取") from exc
        return game
