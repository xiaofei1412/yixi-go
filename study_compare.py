"""Replay immutable study continuations, keeping records distinct from predictions."""
from go_game import coordinate, action_from_coordinate

MAX_COMPARE_MOVES = 12


def replay_line(board, moves):
    board = board.copy()
    captures = {'black': 0, 'white': 0}
    def frame(action=None):
        return {'board': [board.cells[i:i+board.size] for i in range(0, len(board.cells), board.size)],
                'player': board.player, 'action': action, 'captures': captures.copy()}
    frames, played, warning = [frame()], [], ''
    for move in moves[:MAX_COMPARE_MOVES]:
        player = board.player
        opponents = board.cells.count(-player)
        try:
            action = action_from_coordinate(move, board.size)
            board.play(action)
        except ValueError as exc:
            warning = f'后续无法合法回放，已停在有效局面：{exc}'
            break
        captures['black' if player == 1 else 'white'] += opponents - board.cells.count(-player)
        frames.append(frame(action))
        played.append(coordinate(action, board.size))
        if board.passes >= 2:
            warning = '双方连续停着，回放到此结束；未自动判定死活或计分'
            break
    if not warning and len(moves) > MAX_COMPARE_MOVES:
        warning = f'最多展示前 {MAX_COMPARE_MOVES} 手'
    return {'moves': played, 'frames': frames, 'warning': warning}


def candidate_line(board, candidate):
    moves = candidate.get('pv') or [candidate['move']]
    inconsistent = moves[0].lower() != candidate['move'].lower()
    line = replay_line(board, [candidate['move']] if inconsistent else moves)
    line.update(visits=candidate['visits'], black_lead=candidate['scoreLead'])
    if inconsistent:
        line['warning'] = '保存的变化首手不一致，仅展示该候选首手'
    return line


def recorded_continuation(game, node_path):
    # Preserve the selected branch at collection time, including branches that
    # were not child 0. For a manually selected off-route node, follow child 0.
    parts = game.cursor.split('/')
    route = ['/'.join(parts[:i]) for i in range(1, len(parts)+1)]
    while len(game.node(route[-1])):
        route.append(route[-1]+'/0')
    if node_path in route:
        paths = route[route.index(node_path):]
    else:
        paths = [node_path]
        while len(game.node(paths[-1])):
            paths.append(paths[-1]+'/0')
    moves = []
    for path in paths:
        color, point = game.node(path).get_move()
        if color is None:
            continue
        action = game.size**2 if point is None else (game.size-1-point[0])*game.size+point[1]
        moves.append(coordinate(action, game.size))
        if len(moves) > MAX_COMPARE_MOVES:
            break
    return moves
