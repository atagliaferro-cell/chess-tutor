import math
import os
import random
import shutil

import chess
import chess.engine


# The ladder starts at zero so the first defeated bot sets the first visible ELO.
STARTING_PLAYER_ELO = 0


# Centipawn values help the fallback engine compare material in a position.
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}


# Small capture values are used for the chess.com-style captured material display.
CAPTURE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

CAPTURE_DISPLAY_ORDER = {
    chess.QUEEN: 0,
    chess.ROOK: 1,
    chess.BISHOP: 2,
    chess.KNIGHT: 3,
    chess.PAWN: 4,
}


# Common Stockfish locations are checked so the project works on different Macs.
STOCKFISH_PATHS = (
    '/opt/homebrew/bin/stockfish',
    '/usr/local/bin/stockfish',
    '/usr/bin/stockfish',
)


# Each bot ELO uses different Stockfish limits to create difficulty levels.
STOCKFISH_LEVELS = {
    100: {'time': 0.04, 'skill': 0, 'multipv': 7, 'weights': [8, 7, 6, 5, 4, 3, 2], 'random_chance': 0.18},
    250: {'time': 0.06, 'skill': 1, 'multipv': 6, 'weights': [9, 7, 5, 4, 3, 2], 'random_chance': 0.10},
    500: {'time': 0.08, 'skill': 3, 'multipv': 5, 'weights': [10, 7, 4, 2, 1], 'random_chance': 0.04},
    800: {'time': 0.12, 'skill': 6, 'multipv': 3, 'weights': [12, 4, 1], 'random_chance': 0.0},
    1200: {'time': 0.20, 'skill': 10, 'multipv': 2, 'weights': [14, 2], 'random_chance': 0.0},
    2200: {'time': 0.35, 'skill': 16, 'multipv': 1, 'weights': [1], 'random_chance': 0.0},
}

EVALUATION_LIMIT_CP = 1000


# Dialogue categories let tutors respond differently to checks, captures and blunders.
DEFAULT_DIALOGUE_LINES = {
    'blunder': [
        'That move dropped the evaluation. Look for loose pieces before committing.',
        '{san} creates a serious problem. Pause and scan what your opponent can take.',
        'Big warning: the board changed against you. Check captures and checks before moving on.',
    ],
    'missed_capture': [
        'There was a stronger capture available. Compare every capture before choosing one.',
        'You had {best_san}, which wins the {missed_piece}. That is the target to notice.',
        'This is a board vision miss: the bigger prize was still available.',
    ],
    'mistake': [
        'That move gives me a practical chance. Check the threat before the plan.',
        '{san} is legal, but it lets the position slip. Look for the safer improvement.',
        'The idea is close, but the timing is off. Check the opponent reply first.',
    ],
    'strong': [
        'Good move. Your position improved and the pressure is building.',
        '{san} is accurate. You improved the board without creating an obvious weakness.',
        'Strong choice. That move makes your next plan easier.',
    ],
    'check': [
        'Check is useful, but make sure the follow-up is there.',
        '{san} forces the king to respond. Now look for the next forcing move.',
        'Good forcing move. Checks matter most when they improve the position after the reply.',
    ],
    'capture': [
        'You won material. Now keep the piece count under control.',
        '{san} wins a {captured_piece}. Now check whether your piece is safe afterwards.',
        'Good capture. After winning material, trade carefully and avoid giving it back.',
    ],
    'castle': [
        'Good king safety. Now connect the rooks and improve the worst piece.',
        'Castling is useful here. Your king is safer and your rook is closer to the game.',
        'Good defensive habit. King safety gives your attack more time to work.',
    ],
    'promotion': [
        'Promotion changes the whole game. Convert it cleanly.',
        'A promoted pawn is a major advantage. Use the new piece to force the result.',
        'Promotion gives you winning chances. Avoid stalemate tricks and finish calmly.',
    ],
    'opening': [
        'Good opening idea. Build the centre, develop pieces and keep your king safe.',
        '{san} follows opening principles. Keep developing and do not move the same piece too much.',
        'Sound opening move. Now bring another piece out and prepare king safety.',
    ],
    'centre': [
        'Good centre control. That gives your pieces more useful squares.',
        '{san} fights for the middle. Central space makes your next moves easier.',
        'Nice centre move. Owning the middle usually improves every piece.',
    ],
    'development': [
        'Good development. More pieces in the game means more real threats.',
        '{piece} development is useful here. Keep bringing pieces toward active squares.',
        'Good habit: develop first, attack second.',
    ],
    'endgame': [
        'Endgame now. Every pawn move needs a clear reason.',
        'In the endgame, king activity matters. Improve the king and protect passed pawns.',
        'Small moves become big in the endgame. Count pawn races before pushing.',
    ],
    'neutral': [
        'Solid move. Keep checking checks, captures and threats.',
        '{san} is playable. Now ask what your opponent wants next.',
        'Reasonable. Keep improving the worst placed piece.',
    ],
}



# Finds the Stockfish executable before falling back to the built-in simple engine.
def stockfish_path():
    candidates = [
        os.getenv('STOCKFISH_EXECUTABLE'),
        shutil.which('stockfish'),
        *STOCKFISH_PATHS,
    ]

    for path in candidates:
        if path and os.path.exists(path) and os.access(path, os.X_OK):
            return path

    return None



# Creates the session dictionary that stores a live chess game between requests.
def create_game(bot_slug, theme_slug, intro, speaker='Bot', player_color='white', side_chosen=False):
    return {
        'bot_slug': bot_slug,
        'theme_slug': theme_slug,
        'fen': chess.STARTING_FEN,
        'moves': [],
        'dialogue': [{'speaker': speaker, 'text': intro}],
        'last_move': None,
        'status': 'active',
        'winner': None,
        'player_color': player_color if player_color in ('white', 'black') else 'white',
        'side_chosen': side_chosen,
        'engine_source': 'Stockfish' if stockfish_path() else 'Fallback',
    }



# Starts a bot ladder match and lets the bot move first if the user chooses black.
def start_game(bot, theme, player_color):
    game = create_game(
        bot['slug'],
        theme['slug'],
        theme['intro'],
        theme['name'],
        player_color,
        side_chosen=True,
    )

    if player_color == 'black':
        game, _ = apply_bot_move(game, bot, theme)

    return game



# Tutor games keep extra feedback data so the user can learn from each move.
def create_tutor_game(tutor, player_color='white', side_chosen=None):
    tutorial_enabled = tutor.get('tutorial', False)
    if side_chosen is None:
        side_chosen = tutorial_enabled

    game = create_game(
        tutor['slug'],
        tutor['slug'],
        tutor['intro'],
        tutor['name'],
        player_color,
        side_chosen=side_chosen,
    )
    game['mode'] = 'tutor'
    game['tutor_feedback'] = []
    game['tutorial_enabled'] = tutorial_enabled
    return game


def start_tutor_game(tutor, player_color):
    game = create_tutor_game(tutor, player_color, side_chosen=True)

    if player_color == 'black':
        game, _ = apply_tutor_reply_move(game, tutor)

    return game



# Validates and applies a player's move in a normal bot ladder match.
def apply_player_move(game, bot, theme, move_uci):
    board = chess.Board(game['fen'])

    if not game.get('side_chosen'):
        return game, 'Choose white or black before starting the match.'

    if board.is_game_over():
        _set_game_result(game, board)
        return game, 'This game is already finished.'

    if not _is_player_turn(game, board):
        return game, 'Wait for the bot move first.'

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return game, 'That move is not in the correct format.'

    # python-chess protects the game from illegal moves and king captures.
    if move not in board.legal_moves:
        return game, 'Illegal move. Try another square.'

    move_color = _color_name(board.turn)
    player_san = board.san(move)
    move_context = _move_context(game, board, move, player_san)
    board.push(move)
    move_context['is_check'] = board.is_check()
    move_context['is_checkmate'] = board.is_checkmate()
    move_context['category'] = _move_category(move_context)
    game['moves'].append({
        'side': 'player',
        'color': move_color,
        'uci': move.uci(),
        'san': player_san,
        'analysis': _public_move_analysis(move_context),
    })
    game['last_move'] = move.uci()
    game['fen'] = board.fen()

    if board.is_game_over():
        _set_game_result(game, board)
        game['dialogue'].append(_result_dialogue(game, theme))
    else:
        game['dialogue'].append(_player_move_dialogue(game, theme, move_context))

    return game, None



# Tutor moves are compared with the engine recommendation before feedback is shown.
def apply_tutor_player_move(game, tutor, move_uci):
    board = chess.Board(game['fen'])

    if not game.get('side_chosen'):
        return game, 'Choose white or black before starting the tutor game.'

    if board.is_game_over():
        _set_game_result(game, board)
        return game, 'This training game is already finished.'

    if not _is_player_turn(game, board):
        return game, 'Wait for the tutor opponent move first.'

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return game, 'That move is not in the correct format.'

    if move not in board.legal_moves:
        return game, 'Illegal move. Try another square.'

    # The tutor uses the best available move to decide whether feedback is positive.
    best_move, best_source = _tutor_best_move(board)
    best_san = board.san(best_move) if best_move else None
    player_san = board.san(move)
    move_context = _move_context(game, board, move, player_san)
    best_captured_piece = _captured_piece_for_move(board, best_move) if best_move else None
    move_context['best_move'] = best_move.uci() if best_move else None
    move_context['best_san'] = best_san
    move_context['best_is_capture'] = best_captured_piece is not None
    move_context['best_captured_piece_type'] = best_captured_piece.piece_type if best_captured_piece else None
    move_context['engine_source'] = best_source
    move_color = _color_name(board.turn)
    board.push(move)
    move_context['is_check'] = board.is_check()
    move_context['is_checkmate'] = board.is_checkmate()
    move_context['category'] = _move_category(move_context)

    public_analysis = _public_move_analysis(move_context)
    public_analysis['best_move'] = move_context['best_move']
    public_analysis['best_san'] = best_san
    public_analysis['engine_source'] = best_source
    feedback = _tutor_feedback(tutor, move_context, move == best_move, game)

    game['moves'].append({
        'side': 'player',
        'color': move_color,
        'uci': move.uci(),
        'san': player_san,
        'analysis': public_analysis,
        'tutor_feedback': feedback,
    })
    game['last_move'] = move.uci()
    game['fen'] = board.fen()
    game['engine_source'] = best_source
    game.setdefault('tutor_feedback', []).append(feedback)
    game['dialogue'].append({'speaker': tutor['name'], 'text': feedback['message']})

    if board.is_game_over():
        _set_game_result(game, board)
        game['dialogue'].append(_result_dialogue(game, tutor))

    return game, None



# Applies the opponent move after the player has completed their turn.
def apply_bot_move(game, bot, theme):
    board = chess.Board(game['fen'])

    if not game.get('side_chosen'):
        return game, 'Choose white or black before starting the match.'

    if board.is_game_over():
        _set_game_result(game, board)
        return game, None

    if _is_player_turn(game, board):
        return game, 'It is your move.'

    bot_move, engine_source = choose_bot_move(board, bot['elo'])

    if bot_move is None:
        _set_game_result(game, board)
        return game, None

    move_color = _color_name(board.turn)
    bot_san = board.san(bot_move)
    board.push(bot_move)
    game['moves'].append({'side': 'bot', 'color': move_color, 'uci': bot_move.uci(), 'san': bot_san})
    game['last_move'] = bot_move.uci()
    game['fen'] = board.fen()
    game['engine_source'] = engine_source

    if board.is_game_over():
        _set_game_result(game, board)
        game['dialogue'].append(_result_dialogue(game, theme))

    return game, None


def apply_tutor_reply_move(game, tutor):
    board = chess.Board(game['fen'])

    if board.is_game_over():
        _set_game_result(game, board)
        return game, None

    if not _is_bot_turn(game, board):
        return game, 'It is your move.'

    bot_move, engine_source = choose_bot_move(board, tutor['elo'])

    if bot_move is None:
        _set_game_result(game, board)
        return game, None

    bot_color = _color_name(board.turn)
    bot_san = board.san(bot_move)
    board.push(bot_move)
    game['moves'].append({'side': 'bot', 'color': bot_color, 'uci': bot_move.uci(), 'san': bot_san})
    game['last_move'] = bot_move.uci()
    game['fen'] = board.fen()
    game['engine_source'] = engine_source

    if board.is_game_over():
        _set_game_result(game, board)
        game['dialogue'].append(_result_dialogue(game, tutor))

    return game, None



# Rebuilds the board when the tutor lets the user go back after a mistake.
def undo_tutor_move(game, tutor):
    moves = game.get('moves', [])

    if not moves:
        game['dialogue'].append({'speaker': tutor['name'], 'text': 'You are already at the starting position.'})
        return game

    if moves and moves[-1].get('side') == 'bot':
        moves.pop()

    if moves and moves[-1].get('side') == 'player':
        moves.pop()

    board = chess.Board()
    rebuilt_moves = []

    for stored_move in moves:
        try:
            move = chess.Move.from_uci(stored_move['uci'])
        except (KeyError, ValueError):
            break

        if move not in board.legal_moves:
            break

        board.push(move)
        rebuilt_moves.append(stored_move)

    game['moves'] = rebuilt_moves
    game['fen'] = board.fen()
    game['last_move'] = rebuilt_moves[-1]['uci'] if rebuilt_moves else None
    game['status'] = 'active'
    game['winner'] = None
    game['tutor_feedback'] = [
        move['tutor_feedback']
        for move in rebuilt_moves
        if move.get('side') == 'player' and move.get('tutor_feedback')
    ]
    game['dialogue'].append({
        'speaker': tutor['name'],
        'text': 'Position taken back. Try the improved move from here.',
    })
    return game



# Bot move selection prefers Stockfish but still works if Stockfish is unavailable.
def choose_bot_move(board, bot_elo):
    stockfish_move = _stockfish_move(board, bot_elo)

    if stockfish_move:
        return stockfish_move, 'Stockfish'

    return _fallback_move(board, bot_elo), 'Fallback'


def serialize_tutor_game(game, tutor, player_elo=STARTING_PLAYER_ELO):
    state = serialize_game(game, tutor, tutor, player_elo)
    state['mode'] = 'tutor'
    state['can_undo'] = any(move.get('side') == 'player' for move in game.get('moves', []))
    state['tutor_feedback'] = (game.get('tutor_feedback') or [])[-1] if game.get('tutor_feedback') else None
    state['tutorial_enabled'] = game.get('tutorial_enabled', False)
    return state



# Serialises the board into JSON-friendly data for the frontend chessboard.
def serialize_game(game, bot, theme, player_elo=STARTING_PLAYER_ELO):
    board = chess.Board(game['fen'])
    side_chosen = game.get('side_chosen', False)
    player_color = game.get('player_color', 'white')
    pieces = _pieces_for_board(board)

    legal_moves = []
    if game.get('status') == 'active' and side_chosen and _is_player_turn(game, board):
        legal_moves = [move.uci() for move in board.legal_moves]

    return {
        'fen': game['fen'],
        'pieces': pieces,
        'legal_moves': legal_moves,
        'turn': 'white' if board.turn == chess.WHITE else 'black',
        'player_turn': side_chosen and _is_player_turn(game, board) and game.get('status') == 'active',
        'awaiting_bot_move': side_chosen and _is_bot_turn(game, board) and game.get('status') == 'active',
        'awaiting_setup': not side_chosen,
        'player_color': player_color,
        'status': game.get('status', 'active'),
        'winner': game.get('winner'),
        'status_text': _status_text(game, board),
        'move_number': board.fullmove_number,
        'position_number': board.fullmove_number,
        'move_history': game.get('moves', []),
        'notation_log': _notation_log(game.get('moves', [])),
        'position_history': _position_history(game.get('moves', [])),
        'captures': _capture_summary(game.get('moves', [])),
        'evaluation': _evaluation_summary(board),
        'analysis': _analysis_summary(game),
        'dialogue': game.get('dialogue', [])[-8:],
        'last_move': game.get('last_move'),
        'player_elo': player_elo,
        'base_player_elo': player_elo,
        'reward_elo': bot['elo'],
        'engine_source': game.get('engine_source', 'Stockfish' if stockfish_path() else 'Fallback'),
        'bot': {
            'name': bot['name'],
            'elo': bot['elo'],
            'level': bot['level'],
        },
        'theme': {
            'name': theme['name'],
            'avatar': theme['avatar'],
        },
    }



# Requests a move from Stockfish using the settings for the selected bot level.
def _stockfish_move(board, bot_elo):
    path = stockfish_path()

    if not path:
        return None

    config = _stockfish_config(bot_elo)
    legal_moves = list(board.legal_moves)

    if not legal_moves:
        return None

    try:
        with chess.engine.SimpleEngine.popen_uci(path) as engine:
            try:
                engine.configure({'Skill Level': config['skill']})
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
                pass

            if config['multipv'] <= 1:
                result = engine.play(board, chess.engine.Limit(time=config['time']))
                return result.move

            analysis = engine.analyse(
                board,
                chess.engine.Limit(time=config['time']),
                multipv=min(config['multipv'], len(legal_moves)),
            )
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError, FileNotFoundError, OSError, TimeoutError):
        return None

    lines = analysis if isinstance(analysis, list) else [analysis]
    moves = [line['pv'][0] for line in lines if line.get('pv')]

    if not moves:
        return None

    if config['random_chance'] and random.random() < config['random_chance']:
        return random.choice(legal_moves)

    weights = config['weights'][: len(moves)]
    return random.choices(moves, weights=weights, k=1)[0]


def _tutor_best_move(board):
    path = stockfish_path()

    if path:
        try:
            with chess.engine.SimpleEngine.popen_uci(path) as engine:
                result = engine.play(board, chess.engine.Limit(time=0.12))
                if result.move:
                    return result.move, 'Stockfish'
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError, FileNotFoundError, OSError, TimeoutError):
            pass

    return _best_move_for_current_turn(board), 'Fallback'


def _best_move_for_current_turn(board):
    preferred_color = board.turn
    best_score = -math.inf
    best_moves = []

    for move in _ordered_moves(board):
        board.push(move)
        score = _evaluate_for_turn(board, preferred_color)
        board.pop()

        if score > best_score:
            best_score = score
            best_moves = [move]
        elif score == best_score:
            best_moves.append(move)

    return random.choice(best_moves) if best_moves else None


def _stockfish_config(bot_elo):
    for elo in sorted(STOCKFISH_LEVELS):
        if bot_elo <= elo:
            return STOCKFISH_LEVELS[elo]

    return STOCKFISH_LEVELS[2200]



# Fallback move logic keeps games playable even without the Stockfish app installed.
def _fallback_move(board, bot_elo):
    legal_moves = list(board.legal_moves)

    if not legal_moves:
        return None

    if bot_elo <= 100:
        return _beginner_move(board, legal_moves)

    if bot_elo <= 250:
        return _top_scored_move(board, legal_moves, sample_size=5, noise=160)

    if bot_elo <= 500:
        return _top_scored_move(board, legal_moves, sample_size=3, noise=80)

    if bot_elo <= 800:
        return _search_best_move(board, depth=1, noise=30)

    if bot_elo <= 1200:
        return _search_best_move(board, depth=2, noise=20)

    return _search_best_move(board, depth=3, noise=0)


def _beginner_move(board, legal_moves):
    captures = [move for move in legal_moves if board.is_capture(move)]
    checks = [move for move in legal_moves if board.gives_check(move)]

    if captures and random.random() < 0.45:
        return random.choice(captures)

    if checks and random.random() < 0.25:
        return random.choice(checks)

    return random.choice(legal_moves)


def _top_scored_move(board, legal_moves, sample_size, noise):
    scored_moves = []

    for move in legal_moves:
        score = _static_move_score(board, move) + random.randint(-noise, noise)
        scored_moves.append((score, move))

    scored_moves.sort(key=lambda item: item[0], reverse=True)
    return random.choice(scored_moves[: min(sample_size, len(scored_moves))])[1]


def _search_best_move(board, depth, noise):
    best_score = -math.inf
    best_moves = []

    for move in _ordered_moves(board):
        board.push(move)
        score = _minimax(board, depth - 1, -math.inf, math.inf)
        board.pop()

        if noise:
            score += random.randint(-noise, noise)

        if score > best_score:
            best_score = score
            best_moves = [move]
        elif score == best_score:
            best_moves.append(move)

    return random.choice(best_moves)


def _minimax(board, depth, alpha, beta):
    if depth == 0 or board.is_game_over():
        return _evaluate_for_turn(board, chess.BLACK)

    if board.turn == chess.BLACK:
        value = -math.inf
        for move in _ordered_moves(board):
            board.push(move)
            value = max(value, _minimax(board, depth - 1, alpha, beta))
            board.pop()
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return value

    value = math.inf
    for move in _ordered_moves(board):
        board.push(move)
        value = min(value, _minimax(board, depth - 1, alpha, beta))
        board.pop()
        beta = min(beta, value)
        if alpha >= beta:
            break
    return value


def _ordered_moves(board):
    return sorted(board.legal_moves, key=lambda move: _static_move_score(board, move), reverse=True)


def _static_move_score(board, move):
    score = 0

    if board.is_capture(move):
        victim = board.piece_at(move.to_square)
        attacker = board.piece_at(move.from_square)

        if victim is None and board.is_en_passant(move):
            victim_value = PIECE_VALUES[chess.PAWN]
        else:
            victim_value = PIECE_VALUES.get(victim.piece_type, 0) if victim else 0

        attacker_value = PIECE_VALUES.get(attacker.piece_type, 0) if attacker else 0
        score += victim_value * 10 - attacker_value

    if move.promotion:
        score += PIECE_VALUES.get(move.promotion, 0)

    if board.gives_check(move):
        score += 75

    board.push(move)
    score += _evaluate_for_turn(board, chess.BLACK) // 20
    board.pop()

    return score


def _evaluate_for_turn(board, preferred_color):
    outcome = board.outcome()

    if outcome:
        if outcome.winner == preferred_color:
            return 1_000_000
        if outcome.winner is None:
            return 0
        return -1_000_000

    score = 0

    for piece_type, value in PIECE_VALUES.items():
        score += len(board.pieces(piece_type, preferred_color)) * value
        score -= len(board.pieces(piece_type, not preferred_color)) * value

    score += _mobility_score(board, preferred_color)

    if board.is_check():
        score += -35 if board.turn == preferred_color else 35

    return score


def _mobility_score(board, preferred_color):
    turn = board.turn

    board.turn = preferred_color
    preferred_mobility = len(list(board.legal_moves))

    board.turn = not preferred_color
    opponent_mobility = len(list(board.legal_moves))

    board.turn = turn
    return (preferred_mobility - opponent_mobility) * 2



# Builds a compact analysis snapshot for tutor feedback and dialogue.
def _move_context(game, board, move, san):
    piece = board.piece_at(move.from_square)
    captured_piece = _captured_piece_for_move(board, move)
    before_eval = _quick_evaluation_cp(board)
    board_after = board.copy(stack=False)
    board_after.push(move)
    after_eval = _quick_evaluation_cp(board_after)
    player_color = _player_color(game)
    eval_delta = after_eval - before_eval if player_color == chess.WHITE else before_eval - after_eval

    return {
        'san': san,
        'piece_type': piece.piece_type if piece else None,
        'from_square': chess.square_name(move.from_square),
        'to_square': chess.square_name(move.to_square),
        'is_capture': captured_piece is not None,
        'captured_piece_type': captured_piece.piece_type if captured_piece else None,
        'is_castle': board.is_castling(move),
        'is_promotion': move.promotion is not None,
        'promotion_piece_type': move.promotion,
        'phase': _game_phase(board),
        'fullmove_number': board.fullmove_number,
        'eval_before': before_eval,
        'eval_after': after_eval,
        'eval_delta': eval_delta,
        'is_check': False,
        'is_checkmate': False,
        'category': 'neutral',
    }



# Classifies a move so feedback can name blunders, captures, checks and openings.
def _move_category(context):
    eval_delta = context['eval_delta']

    if context['is_checkmate']:
        return 'strong'
    if eval_delta <= -250:
        return 'blunder'
    if eval_delta <= -90:
        return 'mistake'
    if _is_weak_opening_move(context):
        return 'mistake'
    if context['is_promotion']:
        return 'promotion'
    if context['is_check']:
        return 'check'
    if context['is_capture']:
        return 'capture'
    if context['is_castle']:
        return 'castle'
    if eval_delta >= 120:
        return 'strong'
    if context['phase'] == 'endgame':
        return 'endgame'
    if _is_sensible_opening_move(context):
        return 'opening'
    if context['to_square'] in {'d4', 'e4', 'd5', 'e5'}:
        return 'centre'
    if context['phase'] == 'opening' and context['piece_type'] in {chess.KNIGHT, chess.BISHOP}:
        return 'development'

    return 'neutral'


def _is_sensible_opening_move(context):
    if context.get('phase') != 'opening' or context.get('fullmove_number', 99) > 6:
        return False

    if context.get('is_capture') or context.get('is_check') or context.get('is_promotion'):
        return False

    if context.get('is_castle'):
        return True

    piece_type = context.get('piece_type')
    to_square = context.get('to_square', '')
    to_file = to_square[:1]

    if piece_type == chess.PAWN:
        return to_file in {'c', 'd', 'e'}

    if piece_type in {chess.KNIGHT, chess.BISHOP}:
        return True

    return False


def _is_weak_opening_move(context):
    if context.get('phase') != 'opening' or context.get('fullmove_number', 99) > 3:
        return False

    if context.get('is_capture') or context.get('is_check'):
        return False

    if context.get('piece_type') != chess.PAWN:
        return False

    return context.get('to_square') in {'a4', 'a5', 'f3', 'f6', 'g4', 'g5', 'h4', 'h5'}


def _public_move_analysis(context):
    return {
        'category': context['category'],
        'phase': context['phase'],
        'san': context['san'],
        'piece': _piece_name(context['piece_type']),
        'to_square': context['to_square'],
        'is_capture': context['is_capture'],
        'is_check': context['is_check'],
        'is_castle': context['is_castle'],
        'is_promotion': context['is_promotion'],
        'best_is_capture': context.get('best_is_capture', False),
        'best_captured_piece': _piece_name(context.get('best_captured_piece_type')),
        'missed_capture': _missed_stronger_capture(context, matched_best_move=False),
        'eval_before': context['eval_before'],
        'eval_after': context['eval_after'],
        'eval_delta': context['eval_delta'],
    }


def _player_move_dialogue(game, theme, context):
    category = context.get('category', 'neutral')
    theme_lines = theme.get('dialogue_lines', {})
    lines = (
        theme_lines.get(category)
        or theme_lines.get('neutral')
        or DEFAULT_DIALOGUE_LINES.get(category)
        or DEFAULT_DIALOGUE_LINES['neutral']
    )
    player_move_count = len([move for move in game.get('moves', []) if move.get('side') == 'player'])
    line = lines[(player_move_count - 1) % len(lines)]

    return {
        'speaker': theme['name'],
        'text': _format_dialogue_line(line, context),
    }


def _tutor_feedback(tutor, context, matched_best_move, game=None):
    category = context.get('category', 'neutral')
    best_san = context.get('best_san')
    played_san = context.get('san')
    eval_delta = context.get('eval_delta', 0)
    engine_source = context.get('engine_source', 'Stockfish')
    missed_capture = _missed_stronger_capture(context, matched_best_move)
    line_category = 'missed_capture' if missed_capture else category
    theme_lines = tutor.get('dialogue_lines', {})
    base_lines = (
        theme_lines.get(line_category)
        or theme_lines.get(category)
        or DEFAULT_DIALOGUE_LINES.get(line_category)
        or DEFAULT_DIALOGUE_LINES.get(category)
        or theme_lines.get('neutral')
        or DEFAULT_DIALOGUE_LINES['neutral']
    )
    player_move_count = (
        len([move for move in game.get('moves', []) if move.get('side') == 'player']) + 1
        if game
        else 1
    )
    base_line = _format_dialogue_line(base_lines[(player_move_count - 1) % len(base_lines)], context)
    missed_piece = _piece_name(context.get('best_captured_piece_type')).lower()

    if context.get('is_checkmate'):
        title = 'Checkmate'
        severity = 'strong'
        lesson = 'That finishes the game.'
    elif matched_best_move:
        title = 'Best Move'
        severity = 'strong'
        lesson = f'{played_san} matches the engine recommendation.'
    elif category == 'blunder':
        title = 'Blunder'
        severity = 'danger'
        if missed_capture:
            if context.get('is_capture'):
                lesson = f'{played_san} wins material, but it misses the bigger target: {best_san} wins the {missed_piece}. Go back and take it.'
            else:
                lesson = f'{played_san} misses a free {missed_piece}. Go back and try {best_san}.'
        else:
            lesson = f'{played_san} drops too much evaluation. Go back and try {best_san}.'
    elif category == 'mistake':
        title = 'Mistake'
        severity = 'warning'
        if missed_capture:
            if context.get('is_capture'):
                lesson = f'{played_san} wins something, but {best_san} wins the {missed_piece}.'
            else:
                lesson = f'{played_san} is legal, but {best_san} wins the {missed_piece}.'
        else:
            lesson = f'{played_san} is playable, but {best_san} was stronger.'
    elif category == 'opening':
        title = 'Opening'
        severity = 'neutral'
        if best_san and played_san != best_san:
            lesson = f'{played_san} is a sound opening move. {best_san} is the engine preference, but your move follows opening principles.'
        else:
            lesson = f'{played_san} follows opening principles.'
    else:
        title = 'Tutor Note'
        severity = 'neutral'
        lesson = f'{played_san} is reasonable. Engine suggestion: {best_san}.'

    if not best_san:
        lesson = f'{played_san} is legal. Keep checking forcing moves and loose pieces.'

    return {
        'title': title,
        'severity': severity,
        'message': f'{base_line} {lesson}',
        'played_san': played_san,
        'best_san': best_san,
        'played_from': context.get('from_square'),
        'played_to': context.get('to_square'),
        'best_move': context.get('best_move'),
        'best_from': context.get('best_move', '')[:2] or None,
        'best_to': context.get('best_move', '')[2:4] or None,
        'eval_delta': eval_delta,
        'engine_source': engine_source,
    }



# Separates missing a free piece from giving away material.
def _missed_stronger_capture(context, matched_best_move):
    if matched_best_move or not context.get('best_is_capture'):
        return False

    if context.get('category') not in {'blunder', 'mistake'}:
        return False

    played_capture_value = CAPTURE_VALUES.get(context.get('captured_piece_type'), 0)
    best_capture_value = CAPTURE_VALUES.get(context.get('best_captured_piece_type'), 0)

    if not context.get('is_capture'):
        return best_capture_value > 0

    return best_capture_value >= played_capture_value + 2


def _format_dialogue_line(line, context):
    values = {
        'san': context.get('san', 'that move'),
        'piece': _piece_name(context.get('piece_type')).lower(),
        'square': context.get('to_square', ''),
        'captured_piece': _piece_name(context.get('captured_piece_type')).lower(),
        'missed_piece': _piece_name(context.get('best_captured_piece_type')).lower(),
        'best_san': context.get('best_san') or 'the engine move',
        'eval_delta': _format_eval_delta(context.get('eval_delta')),
    }

    try:
        return line.format(**values)
    except (KeyError, ValueError):
        return line


def _format_eval_delta(eval_delta):
    if eval_delta is None:
        return '0.0'

    sign = '+' if eval_delta >= 0 else '-'
    return f'{sign}{abs(eval_delta) / 100:.1f}'


def _game_phase(board):
    piece_count = len(board.piece_map())

    if piece_count <= 12:
        return 'endgame'
    if board.fullmove_number <= 10:
        return 'opening'
    return 'middlegame'


def _quick_evaluation_cp(board):
    score = _engine_score(board, time_limit=0.035)

    if score['cp'] is not None:
        return score['cp']

    mate = score.get('mate')
    if mate is None:
        return 0
    if mate == 0:
        return 0

    return EVALUATION_LIMIT_CP if mate > 0 else -EVALUATION_LIMIT_CP


def _evaluation_summary(board):
    score = _engine_score(board, time_limit=0.04)
    mate = score.get('mate')

    if mate is not None:
        white_percent = 95 if mate > 0 else 5
        label = 'White mate' if mate > 0 else 'Black mate'

        if abs(mate) > 1:
            label = f'White M{abs(mate)}' if mate > 0 else f'Black M{abs(mate)}'

        return {
            'white_percent': white_percent,
            'black_percent': 100 - white_percent,
            'label': label,
            'cp': None,
            'mate': mate,
            'source': score['source'],
        }

    cp = score['cp'] or 0
    display_cp = int(_clamp(cp, -EVALUATION_LIMIT_CP, EVALUATION_LIMIT_CP))
    white_percent = round(_clamp(50 + (display_cp / 20), 5, 95), 1)

    if abs(cp) < 25:
        label = 'Even'
    elif cp > 0:
        label = f'White +{abs(cp) / 100:.1f}'
    else:
        label = f'Black +{abs(cp) / 100:.1f}'

    return {
        'white_percent': white_percent,
        'black_percent': round(100 - white_percent, 1),
        'label': label,
        'cp': cp,
        'mate': None,
        'source': score['source'],
    }


def _engine_score(board, time_limit=0.04):
    outcome = board.outcome()

    if outcome:
        if outcome.winner is None:
            return {'cp': 0, 'mate': None, 'source': 'Game'}
        if board.is_checkmate():
            return {'cp': None, 'mate': 1 if outcome.winner == chess.WHITE else -1, 'source': 'Game'}
        return {
            'cp': EVALUATION_LIMIT_CP if outcome.winner == chess.WHITE else -EVALUATION_LIMIT_CP,
            'mate': None,
            'source': 'Game',
        }

    path = stockfish_path()

    if path:
        try:
            with chess.engine.SimpleEngine.popen_uci(path) as engine:
                info = engine.analyse(board, chess.engine.Limit(time=time_limit))
                score = info['score'].pov(chess.WHITE)
                mate = score.mate()

                if mate is not None:
                    return {'cp': None, 'mate': mate, 'source': 'Stockfish'}

                cp = score.score(mate_score=100000)
                return {'cp': int(cp or 0), 'mate': None, 'source': 'Stockfish'}
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError, FileNotFoundError, OSError, TimeoutError):
            pass

    return {'cp': _evaluate_for_turn(board, chess.WHITE), 'mate': None, 'source': 'Fallback'}



# Converts the completed game into skill scores for the dashboard.
def _analysis_summary(game):
    player_moves = [
        move for move in game.get('moves', [])
        if move.get('side') == 'player' and move.get('analysis')
    ]

    if not player_moves:
        return {
            'available': False,
            'summary': 'Analysis unlocks after you complete a game.',
            'metrics': [],
        }

    metrics = _skill_metrics(player_moves)

    if game.get('status') == 'active':
        return {
            'available': False,
            'summary': 'Finish the game to unlock full analysis.',
            'metrics': [],
        }

    best = max(metrics, key=lambda metric: metric['score'])
    focus = min(metrics, key=lambda metric: metric['score'])

    if game.get('winner') == 'player':
        result_text = 'Win recorded.'
    elif game.get('winner') == 'bot':
        result_text = 'Game complete.'
    else:
        result_text = 'Draw recorded.'

    return {
        'available': True,
        'summary': f'{result_text} Strongest area: {best["name"]}. Training focus: {focus["name"]}.',
        'metrics': metrics,
    }


def _skill_metrics(player_moves):
    analyses = [move['analysis'] for move in player_moves]
    deltas = [analysis.get('eval_delta', 0) for analysis in analyses]
    avg_loss = sum(max(0, -delta) for delta in deltas) / max(len(deltas), 1)
    categories = [analysis.get('category', 'neutral') for analysis in analyses]
    blunders = categories.count('blunder')
    mistakes = categories.count('mistake')
    captures = sum(1 for analysis in analyses if analysis.get('is_capture'))
    checks = sum(1 for analysis in analyses if analysis.get('is_check'))
    castles = sum(1 for analysis in analyses if analysis.get('is_castle'))
    strong_moves = categories.count('strong')
    early = analyses[:8] or analyses
    early_good = sum(
        1 for analysis in early
        if analysis.get('category') in {'centre', 'development', 'castle', 'strong'}
    )
    early_bad = sum(1 for analysis in early if analysis.get('category') in {'mistake', 'blunder'})
    endgame_moves = [analysis for analysis in analyses if analysis.get('phase') == 'endgame']

    accuracy = _score(100 - (avg_loss / 5) - (blunders * 8) - (mistakes * 3))
    openings = _score(50 + (early_good * 10) - (early_bad * 15))
    tactics = _score(45 + (captures * 7) + (checks * 11) + (strong_moves * 8) - (blunders * 8))
    defence = _score(88 - (blunders * 18) - (mistakes * 8) + (castles * 10))
    board_vision = _score(accuracy + (captures * 2) - (blunders * 6))
    puzzle_solving = _score(tactics + (checks * 3) - (mistakes * 4))
    aggression = _score(38 + (captures * 8) + (checks * 12) + (strong_moves * 7))

    if endgame_moves:
        endgame_bad = sum(1 for analysis in endgame_moves if analysis.get('category') in {'mistake', 'blunder'})
        endgames = _score(65 - (endgame_bad * 12) + (len(endgame_moves) * 2))
    else:
        endgames = 50

    return [
        {'name': 'Openings', 'score': openings},
        {'name': 'Tactics', 'score': tactics},
        {'name': 'Endgames', 'score': endgames},
        {'name': 'Defence', 'score': defence},
        {'name': 'Board Vision', 'score': board_vision},
        {'name': 'Puzzle Solving', 'score': puzzle_solving},
        {'name': 'Accuracy', 'score': accuracy},
        {'name': 'Aggression', 'score': aggression},
    ]


def _score(value):
    return int(round(_clamp(value, 0, 100)))


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _piece_name(piece_type):
    names = {
        chess.PAWN: 'Pawn',
        chess.KNIGHT: 'Knight',
        chess.BISHOP: 'Bishop',
        chess.ROOK: 'Rook',
        chess.QUEEN: 'Queen',
        chess.KING: 'King',
    }
    return names.get(piece_type, 'Piece')


def _set_game_result(game, board):
    game['fen'] = board.fen()

    if board.is_checkmate():
        winner_color = not board.turn
        game['status'] = 'checkmate'
        game['winner'] = 'player' if winner_color == _player_color(game) else 'bot'
    elif board.is_stalemate():
        game['status'] = 'stalemate'
        game['winner'] = 'draw'
    elif board.is_insufficient_material():
        game['status'] = 'draw'
        game['winner'] = 'draw'
    elif board.is_seventyfive_moves() or board.is_fivefold_repetition():
        game['status'] = 'draw'
        game['winner'] = 'draw'
    else:
        game['status'] = 'active'
        game['winner'] = None


def _status_text(game, board):
    if not game.get('side_chosen'):
        return 'Choose your side'
    if game.get('winner') == 'player':
        return 'Checkmate. You won.'
    if game.get('winner') == 'bot':
        return 'Checkmate. Bot won.'
    if game.get('winner') == 'draw':
        return 'Draw.'

    prefix = 'Check. ' if board.is_check() else ''
    return prefix + ('Your move' if _is_player_turn(game, board) else 'Bot thinking')


def _move_dialogue(game, theme):
    lines = theme.get('dialogue_lines', {}).get('neutral') or DEFAULT_DIALOGUE_LINES['neutral']
    bot_moves = len([move for move in game.get('moves', []) if move['side'] == 'bot'])
    return {'speaker': theme['name'], 'text': lines[(bot_moves - 1) % len(lines)]}


def _result_dialogue(game, theme):
    result_lines = theme.get('result_lines', {})

    if game.get('winner') == 'player':
        return {'speaker': theme['name'], 'text': result_lines.get('player_win', 'You got the checkmate. Fair play.')}
    if game.get('winner') == 'bot':
        return {'speaker': theme['name'], 'text': result_lines.get('bot_win', 'Checkmate. Run it back and find the mistake.')}
    return {'speaker': theme['name'], 'text': result_lines.get('draw', 'Draw. Nobody gets the last word this time.')}


def _pieces_for_board(board):
    pieces = []

    for square, piece in board.piece_map().items():
        color = 'w' if piece.color == chess.WHITE else 'b'
        pieces.append(
            {
                'square': chess.square_name(square),
                'piece': f'{color}{piece.symbol().lower()}',
            }
        )

    return pieces


def _position_history(moves):
    board = chess.Board()
    history = [
        {
            'index': 0,
            'label': 'Starting Position',
            'san': '',
            'side': '',
            'last_move': None,
            'pieces': _pieces_for_board(board),
        }
    ]

    for index, stored_move in enumerate(moves, start=1):
        try:
            move = chess.Move.from_uci(stored_move['uci'])
        except (KeyError, ValueError):
            break

        if move not in board.legal_moves:
            break

        move_number = board.fullmove_number
        move_color = board.turn
        san = stored_move.get('san') or board.san(move)
        board.push(move)

        history.append(
            {
                'index': index,
                'label': _position_history_label(move_number, move_color, san),
                'san': san,
                'side': stored_move.get('side', ''),
                'last_move': move.uci(),
                'pieces': _pieces_for_board(board),
            }
        )

    return history


def _position_history_label(move_number, move_color, san):
    if move_color == chess.WHITE:
        return f'{move_number}. {san}'

    return f'{move_number}... {san}'



# Formats stored moves into standard chess notation for review screens.
def _notation_log(moves):
    board = chess.Board()
    rows = []

    for stored_move in moves:
        try:
            move = chess.Move.from_uci(stored_move['uci'])
        except (KeyError, ValueError):
            break

        if move not in board.legal_moves:
            break

        move_number = board.fullmove_number
        move_color = board.turn
        san = stored_move.get('san') or board.san(move)

        if move_color == chess.WHITE:
            rows.append({'number': move_number, 'white': san, 'black': ''})
        elif rows and rows[-1]['number'] == move_number:
            rows[-1]['black'] = san
        else:
            rows.append({'number': move_number, 'white': '...', 'black': san})

        board.push(move)

    return rows



# Builds the captured-piece totals shown beside the board.
def _capture_summary(moves):
    board = chess.Board()
    captured_by_white = []
    captured_by_black = []

    for stored_move in moves:
        try:
            move = chess.Move.from_uci(stored_move['uci'])
        except (KeyError, ValueError):
            break

        if move not in board.legal_moves:
            break

        captured_piece = _captured_piece_for_move(board, move)

        if captured_piece:
            if board.turn == chess.WHITE:
                captured_by_white.append(captured_piece)
            else:
                captured_by_black.append(captured_piece)

        board.push(move)

    white_material = sum(CAPTURE_VALUES[piece.piece_type] for piece in captured_by_white)
    black_material = sum(CAPTURE_VALUES[piece.piece_type] for piece in captured_by_black)
    balance = white_material - black_material

    return {
        'white': {
            'pieces': _captured_piece_codes(captured_by_white),
            'material': white_material,
            'advantage': max(balance, 0),
        },
        'black': {
            'pieces': _captured_piece_codes(captured_by_black),
            'material': black_material,
            'advantage': max(-balance, 0),
        },
    }


def _captured_piece_for_move(board, move):
    if not board.is_capture(move):
        return None

    if board.is_en_passant(move):
        captured_square = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        return board.piece_at(captured_square)

    return board.piece_at(move.to_square)


def _captured_piece_codes(pieces):
    ordered_pieces = sorted(
        pieces,
        key=lambda piece: (CAPTURE_DISPLAY_ORDER.get(piece.piece_type, 99), piece.color),
    )

    return [
        f'{"w" if piece.color == chess.WHITE else "b"}{piece.symbol().lower()}'
        for piece in ordered_pieces
    ]


def _player_color(game):
    return chess.WHITE if game.get('player_color', 'white') == 'white' else chess.BLACK


def _is_player_turn(game, board):
    return board.turn == _player_color(game)


def _is_bot_turn(game, board):
    return board.turn != _player_color(game)


def _color_name(color):
    return 'white' if color == chess.WHITE else 'black'
