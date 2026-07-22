import json
import math
import chess
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.conf import settings
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.views.decorators.http import require_POST
from smtplib import SMTPException
from .models import PuzzleRatingAttempt, UserProfile
from .chess_engine import (
    STARTING_PLAYER_ELO,
    apply_bot_move,
    apply_player_move,
    apply_tutor_player_move,
    apply_tutor_reply_move,
    create_game,
    create_tutor_game,
    serialize_game,
    serialize_tutor_game,
    start_game,
    start_tutor_game,
    undo_tutor_move,
)
from .forms import ChessTutorUserCreationForm, ProfilePictureForm

# Uses Django's active User model so authentication stays compatible with settings.
User = get_user_model()
# Session keys keep live games separate from saved database profile data.
GAME_SESSION_KEY = 'active_chess_game'
TUTOR_SESSION_KEY = 'active_tutor_game'
PROGRESS_SESSION_KEY = 'chess_tutor_progress'



# Builds the six-level bot ladder and marks which bots are locked or defeated.
def get_bot_ladder(progress=None):
    bots = [
        {
            'number': '01',
            'slug': 'pawn-hunter',
            'name': 'Pawn Hunter',
            'avatar': 'main/ladder_bots/pawn-hunter.png',
            'elo': 100,
            'level': 'Easy',
            'status': 'Unlocked',
            'description': 'The first ladder bot. Beat this level to unlock the next challenge.',
            'intro': 'Pick a theme, then beat the bot to reach 100 ELO.',
            'unlocked': True,
            'active': True,
            'progress_width': 16,
        },
        {
            'number': '02',
            'slug': 'knight-scout',
            'name': 'Knight Scout',
            'avatar': 'main/ladder_bots/knight-scout.png',
            'elo': 250,
            'level': 'Beginner',
            'status': 'Locked',
            'description': 'Unlock by beating Pawn Hunter.',
            'intro': 'Beat the first bot to unlock this match.',
            'unlocked': False,
            'active': False,
            'progress_width': 0,
        },
        {
            'number': '03',
            'slug': 'bishop-blade',
            'name': 'Bishop Blade',
            'avatar': 'main/ladder_bots/bishop-blade.png',
            'elo': 500,
            'level': 'Intermediate',
            'status': 'Locked',
            'description': 'Unlock by beating Knight Scout.',
            'intro': 'Beat Knight Scout to unlock this match.',
            'unlocked': False,
            'active': False,
            'progress_width': 0,
        },
        {
            'number': '04',
            'slug': 'rook-guard',
            'name': 'Rook Guard',
            'avatar': 'main/ladder_bots/rook-guard.png',
            'elo': 800,
            'level': 'Strong',
            'status': 'Locked',
            'description': 'Unlock by beating Bishop Blade.',
            'intro': 'Beat Bishop Blade to unlock this match.',
            'unlocked': False,
            'active': False,
            'progress_width': 0,
        },
        {
            'number': '05',
            'slug': 'queen-hunter',
            'name': 'Queen Hunter',
            'avatar': 'main/ladder_bots/queen-hunter.png',
            'elo': 1200,
            'level': 'Advanced',
            'status': 'Locked',
            'description': 'Unlock by beating Rook Guard.',
            'intro': 'Beat Rook Guard to unlock this match.',
            'unlocked': False,
            'active': False,
            'progress_width': 0,
        },
        {
            'number': '06',
            'slug': 'king-slayer',
            'name': 'King Slayer',
            'avatar': 'main/ladder_bots/king-slayer.png',
            'elo': 2200,
            'level': 'Insane',
            'status': 'Locked',
            'description': 'Final bot. Unlock by clearing the ladder.',
            'intro': 'Clear the ladder to unlock the final match.',
            'unlocked': False,
            'active': False,
            'progress_width': 0,
        },
    ]


    # Without progress, the ladder returns its default starting state.
    if progress is None:
        return bots

    defeated_bots = set(progress.get('defeated_bots', []))
    unlocked_count = min(len(defeated_bots) + 1, len(bots))
    active_index = min(len(defeated_bots), len(bots) - 1)

    for index, bot in enumerate(bots):
        bot['unlocked'] = index < unlocked_count
        bot['active'] = index == active_index

        if bot['slug'] in defeated_bots:
            bot['status'] = 'Defeated'
            bot['progress_width'] = 100
        elif bot['unlocked']:
            bot['status'] = 'Unlocked'
            bot['progress_width'] = 16
        else:
            bot['status'] = 'Locked'
            bot['progress_width'] = 0

    return bots



# Player ELO is the highest bot level defeated rather than a running total.
def calculate_player_elo(defeated_bots):
    bot_elos = {
        bot['slug']: bot['elo']
        for bot in get_bot_ladder()
    }
    return max(
        [STARTING_PLAYER_ELO] + [
            bot_elos[bot_slug]
            for bot_slug in defeated_bots
            if bot_slug in bot_elos
        ]
    )



# Themes change the personality and dialogue without changing the bot's rating.
def get_bot_themes():
    return [
        {
            'slug': 'drizzy-drake',
            'name': 'Drizzy Drake',
            'avatar': 'main/bots/drizzy-drake.png',
            'style': 'Smooth confidence',
            'intro': "Yo, it's Drizzy. Keep it smooth, no panic moves, no free pieces.",
            'chat_lines': [
                "Opening move looking clean. I see you trying to set the tone.",
                "That centre control is smooth. Do not fumble it now.",
                "Damn, that was a good move. You might be locked in.",
                "You gave me a square for free. That is not chess, that is charity.",
                "Quiet move, loud threat. I see the plan.",
                "Your knight is stranded. Get your pieces back in the group chat.",
                "That capture looked tempting, but the board always keeps receipts.",
                "Endgame coming up. Time to prove you were not just talking.",
            ],
            'dialogue_lines': {
                'blunder': [
                    "That move cost you, no lie. Check what's hanging before you flex.",
                    "{san} gave the position away too easily. Slow it down and scan the board.",
                    "That one was not smooth. Your opponent has a clean reply now.",
                ],
                'missed_capture': [
                    "You missed the bigger bag: {best_san} wins the {missed_piece}.",
                    "The free {missed_piece} was sitting there. Do not leave that on the table.",
                    "{san} is not the move when {best_san} wins real material.",
                ],
                'mistake': [
                    "I see the idea, but the board is not buying it yet. Tighten it up.",
                    "{san} is playable, but it lets the pressure cool off.",
                    "Not a disaster, but you gave me room. Keep the threats tighter.",
                ],
                'strong': [
                    "Damn, that was clean. You improved the position and kept the pressure smooth.",
                    "{san} is smooth. You made the board easier to play.",
                    "That move has confidence. Keep the plan simple now.",
                ],
                'check': [
                    "Check is loud, but the follow-up has to sing too.",
                    "{san} makes the king answer you. Now find the next pressure move.",
                    "Good forcing move. Do not stop calculating after the check.",
                ],
                'capture': [
                    "You took material. Now do not give it straight back.",
                    "Good pickup. After {san}, protect what you just won.",
                    "Material won. Keep it smooth and trade smart.",
                ],
                'castle': [
                    "King tucked away. Smooth. Now bring the rest of the crew in.",
                    "Good safety move. You got the king out of the drama.",
                    "That castle keeps the position calm. Now improve the worst piece.",
                ],
                'promotion': [
                    "New queen energy. Convert this like you meant it.",
                    "Promotion changes the whole song. Finish it clean.",
                    "You made it to the end. Now do not let stalemate ruin the story.",
                ],
                'opening': [
                    "Clean opening idea. Centre, development, king safety. Keep that order.",
                    "{san} is a fine start. Do not get fancy before your pieces are out.",
                    "Opening looks smooth. Build the position before chasing tactics.",
                ],
                'centre': [
                    "Centre control looking smooth. That is how you set the tone.",
                    "{san} takes space where it matters. Good start.",
                    "You touched the middle. Now develop around it.",
                ],
                'development': [
                    "Good development. Everybody is getting in the group chat now.",
                    "{piece} out early is clean. Keep bringing pieces into the game.",
                    "More pieces active, more chances to create threats.",
                ],
                'endgame': [
                    "Endgame now. No panic moves, just clean conversion.",
                    "Small board now. Every pawn move has to mean something.",
                    "Keep it steady. Endgames punish rushed flexes.",
                ],
                'neutral': [
                    "Solid. Keep scanning checks, captures and threats.",
                    "{san} is calm. Now ask what I want next.",
                    "Fine move. Keep the board under control.",
                ],
            },
            'result_lines': {
                'player_win': "You got the mate. Fair play, that was smooth.",
                'bot_win': "Checkmate. Run it back, because that one got away from you.",
                'draw': "Draw. Respectable, but next time finish the story.",
            },
        },
        {
            'slug': 'lebron',
            'name': 'LeBron',
            'avatar': 'main/bots/lebron.png',
            'style': 'Championship focus',
            'intro': "Big stage. First move is tip-off. Make smart plays and protect the board.",
            'chat_lines': [
                "Good court vision. You saw the whole board before moving.",
                "That move puts pressure in the paint. I respect it.",
                "Do not force the attack. Make the right pass first.",
                "You just left a piece hanging. That is a turnover.",
                "Your knight found a lane. That is strong rotation.",
                "Castle soon. Defence wins championships.",
                "That tactic is a fast break. Finish it clean.",
                "Fourth quarter position now. Close the game properly.",
            ],
            'dialogue_lines': {
                'blunder': [
                    "That was a turnover. You gave me the ball with no pressure.",
                    "{san} is a bad possession. You need to see the reply before you move.",
                    "That move lost control of the floor. Reset and defend.",
                ],
                'missed_capture': [
                    "You missed the open dunk: {best_san} wins the {missed_piece}.",
                    "That {missed_piece} was free in the lane. Take the easy points.",
                    "{san} passes up material. {best_san} was the stronger play.",
                ],
                'mistake': [
                    "Not terrible, but the rotation was late. Check the threat first.",
                    "{san} is okay, but the defence was not set.",
                    "You forced it a bit. Make the simple pass first.",
                ],
                'strong': [
                    "Good court vision. You saw the board and made the right play.",
                    "{san} is a smart possession. Clean, patient, strong.",
                    "That move creates pressure without over-dribbling.",
                ],
                'check': [
                    "That check puts pressure on the rim. Now finish the possession.",
                    "{san} forces the defence to move. Look for the follow-up.",
                    "Good attack. Keep the king under pressure.",
                ],
                'capture': [
                    "Good steal. Turn that material into points.",
                    "{san} wins material. Now protect the lead.",
                    "Clean pickup. Do not give the ball right back.",
                ],
                'castle': [
                    "Defence wins championships. King safety first.",
                    "Good rotation. The king is safer and the rook is ready.",
                    "You got your defence organised. Now build the attack.",
                ],
                'promotion': [
                    "That promotion is superstar minutes. Close the game.",
                    "New queen on the court. Use it to finish.",
                    "You earned the promotion. No sloppy possessions now.",
                ],
                'opening': [
                    "Good opening set. Centre first, pieces next, king safe after.",
                    "{san} is a solid first play. Keep developing.",
                    "Nice start. Do not force the highlight play too early.",
                ],
                'centre': [
                    "You took the middle like the paint. Strong positioning.",
                    "{san} controls the lane. Good board presence.",
                    "Middle control gives you better passing angles.",
                ],
                'development': [
                    "Good rotation. More pieces involved, more options open.",
                    "{piece} development gives the team more movement.",
                    "Bring everyone into the play. One piece cannot win alone.",
                ],
                'endgame': [
                    "Fourth quarter now. No rushed shots.",
                    "Endgame possession. Value every pawn like a point.",
                    "Close it like a champion. Calculate before pushing.",
                ],
                'neutral': [
                    "Steady play. Make the simple pass before forcing anything.",
                    "{san} keeps the game moving. Keep scanning.",
                    "Good enough. Now look for the next weakness.",
                ],
            },
            'result_lines': {
                'player_win': "Checkmate. Big-time finish.",
                'bot_win': "Game over. Watch the replay and fix the turnover.",
                'draw': "Draw. Overtime energy, but nobody closed it.",
            },
        },
        {
            'slug': 'd-mcguire',
            'name': 'D McGuire',
            'avatar': 'main/bots/d-mcguire.png',
            'style': 'Software teacher',
            'intro': 'Think through the algorithm before you move. No random clicks in production.',
            'chat_lines': [
                'Trace the logic first. What does this move actually return?',
                'Good move. Clean structure, low risk, readable plan.',
                'That tactic has a bug. You forgot to validate the threat.',
                'Watch the edge case on the back rank. That is where programs crash.',
                'Your queen is doing too much. Reduce the dependencies.',
                'That move passes the eye test, but does it pass the test case?',
                'Nice debugging. You found the loose piece before it caused a runtime error.',
                'This position needs refactoring. Move the worst-placed piece first.',
            ],
            'dialogue_lines': {
                'blunder': [
                    'That move failed validation. A loose piece just slipped through production.',
                    '{san} introduces a major bug. Run the threat check before deploying moves.',
                    'That is a broken build. The position no longer passes the material test.',
                ],
                'missed_capture': [
                    'You skipped the obvious test case: {best_san} wins the {missed_piece}.',
                    'The free {missed_piece} was sitting in the bug report. Fix that first.',
                    '{san} compiles, but {best_san} is the cleaner solution.',
                ],
                'mistake': [
                    'The logic is close, but there is an edge case you did not handle.',
                    '{san} is not fatal, but the test coverage is weak.',
                    'That move needs refactoring. Check the opponent response.',
                ],
                'strong': [
                    'Good move. Clean structure, low risk, and the plan is readable.',
                    '{san} passes the test suite. Strong, simple, maintainable.',
                    'Nice debugging. You improved the position without creating a new issue.',
                ],
                'check': [
                    'That check is a useful test case. Now prove the continuation works.',
                    '{san} forces a response. Trace the next branch.',
                    'Good forcing move. Keep calculating the return value.',
                ],
                'capture': [
                    'Nice debugging. You found the loose piece and removed it.',
                    '{san} wins a {captured_piece}. Now make sure there is no regression.',
                    'Good material capture. Protect the asset you just gained.',
                ],
                'castle': [
                    'Good security patch. The king is no longer sitting in public scope.',
                    'Nice defensive update. King safety dependency resolved.',
                    'Castling cleans up the architecture. Now connect the rooks.',
                ],
                'promotion': [
                    'Promotion deployed. Now do not crash the release.',
                    'Pawn upgraded successfully. Convert the advantage without introducing stalemate.',
                    'That promotion is a major version update. Use it carefully.',
                ],
                'opening': [
                    'Good opening architecture. Centre, development, king safety.',
                    '{san} is a stable opening commit. Continue developing the pieces.',
                    'Clean start. Do not over-engineer before the pieces are active.',
                ],
                'centre': [
                    'Good control flow through the centre. Your pieces have better routes now.',
                    '{san} improves the main path through the board.',
                    'Centre control gives your pieces cleaner routing.',
                ],
                'development': [
                    'Good modular design. You brought another piece into the system.',
                    '{piece} development reduces your inactive code.',
                    'Another piece online. The system has more working parts now.',
                ],
                'endgame': [
                    'Endgame logic now. Avoid off-by-one pawn mistakes.',
                    'Small errors scale fast in this phase. Count every pawn race.',
                    'Endgame branch. The king becomes an active function now.',
                ],
                'neutral': [
                    'Reasonable line. Keep tracing checks, captures and threats.',
                    '{san} is stable. Now inspect what the opponent threatens.',
                    'No crash there. Keep improving the weakest piece.',
                ],
            },
            'result_lines': {
                'player_win': 'Checkmate. That solution passes the final test case.',
                'bot_win': 'Checkmate. Review the bug report and patch the weakness.',
                'draw': 'Draw. Stable build, but no winning feature shipped.',
            },
        },
        {
            'slug': 'serena-williams',
            'name': 'Serena Williams',
            'avatar': 'main/bots/serena-williams.png',
            'style': 'Competitive discipline',
            'intro': 'Every move needs intent. Serve pressure from move one.',
            'chat_lines': [
                'Strong serve. You took the centre early.',
                'Stay composed. Pressure only matters if you control it.',
                'That move pushed me behind the baseline. Nice timing.',
                'Do not rush the winner. Build the point first.',
                'Better footwork. Improve the piece before swinging.',
                'That tactic had pace. Follow through now.',
                'You gave me a free point there. Reset and defend.',
                'Match point energy now. Finish with discipline.',
            ],
            'dialogue_lines': {
                'blunder': [
                    'Free point. Reset quickly and protect the next rally.',
                    '{san} gives away too much. Recover your balance before the next move.',
                    'That was rushed. The position punished the swing.',
                ],
                'missed_capture': [
                    'You had a clean winner: {best_san} takes the {missed_piece}.',
                    'The {missed_piece} was open court. Step in and take it.',
                    '{san} keeps the rally going, but {best_san} wins the point.',
                ],
                'mistake': [
                    'You rushed that shot. Build the point before swinging.',
                    '{san} is playable, but it gives up pressure.',
                    'The timing was slightly early. Check the reply before committing.',
                ],
                'strong': [
                    'Strong move. Controlled pressure, clean timing.',
                    '{san} has discipline. You improved without overreaching.',
                    'Excellent placement. That move makes the next one easier.',
                ],
                'check': [
                    'That check has pace. Follow through with discipline.',
                    '{san} forces a defensive return. Be ready for the next shot.',
                    'Good pressure. Now calculate the reply.',
                ],
                'capture': [
                    'Good winner. You earned material and kept balance.',
                    '{san} wins a {captured_piece}. Stay composed after the point.',
                    'Clean capture. Now hold the advantage.',
                ],
                'castle': [
                    'Composed defence. Now step in and take space.',
                    'Good reset. The king is safer and you can play forward.',
                    'Defence handled. Now bring pressure back.',
                ],
                'promotion': [
                    'Promotion on match point. Finish it cleanly.',
                    'New queen, big pressure. Close with discipline.',
                    'Promotion is earned. Do not rush the final point.',
                ],
                'opening': [
                    'Good first serve. Centre, development, king safety.',
                    '{san} starts the rally well. Keep the pieces coordinated.',
                    'Nice opening rhythm. Build before attacking.',
                ],
                'centre': [
                    'Strong serve into the centre. You are controlling the rally.',
                    '{san} takes central space. That limits the opponent response.',
                    'Centre control gives you better angles.',
                ],
                'development': [
                    'Better footwork. Your piece is now in the match.',
                    '{piece} development improves your court coverage.',
                    'Good movement. Active pieces create pressure.',
                ],
                'endgame': [
                    'Match point chess. Every move needs intent.',
                    'Endgame now. Stay calm and convert one step at a time.',
                    'Small advantage, big discipline. Count the pawn race.',
                ],
                'neutral': [
                    'Stay composed. Pressure only matters if you control it.',
                    '{san} keeps balance. Now look for the next target.',
                    'Fine move. Keep your timing clean.',
                ],
            },
            'result_lines': {
                'player_win': 'Checkmate. Clinical finish.',
                'bot_win': 'Game, set, match. Learn from the pressure point.',
                'draw': 'Draw. Long rally, no winner.',
            },
        },
        {
            'slug': 'gordon-ramsay',
            'name': 'Gordon Ramsay',
            'avatar': 'main/bots/gordon-ramsay.png',
            'style': 'Chef intensity',
            'intro': 'Keep the position clean. One sloppy move and this whole board is undercooked.',
            'chat_lines': [
                'Your opening has no seasoning. Develop your pieces.',
                'Finally, a move with flavour. Keep cooking.',
                'That pawn structure is a mess. Clean the kitchen.',
                'That position needs cleaning up. That is raw.',
                'Your bishop is sitting in the pantry. Put it to work.',
                'You cannot garnish a blunder. Fix the position first.',
                'That tactic is cooked perfectly. Serve the attack.',
                'Now plate the checkmate properly. Do not burn it at the finish.',
            ],
            'dialogue_lines': {
                'blunder': [
                    'That move is an idiot sandwich: two slices of hope around a blunder.',
                    '{san} is raw. You served the opponent a free chance.',
                    'That is not a plan, that is a kitchen fire. Check the tactics first.',
                ],
                'missed_capture': [
                    'The {missed_piece} was free. {best_san} was the dish, and you left it in the kitchen.',
                    'You ignored the main ingredient. {best_san} wins the {missed_piece}.',
                    '{san} is garnish. {best_san} is the meal.',
                ],
                'mistake': [
                    'That was under-seasoned. The idea is there, but the position is not ready.',
                    '{san} is edible, but it is not winning service.',
                    'You rushed the recipe. Check the opponent reply first.',
                ],
                'strong': [
                    'Finally, a move with flavour. Keep cooking.',
                    '{san} is properly cooked. Now keep the position clean.',
                    'That is more like it. Simple move, strong flavour.',
                ],
                'check': [
                    'That check has heat. Do not burn the follow-up.',
                    '{san} puts the king under the grill. Now finish the recipe.',
                    'Good check. Calculate the next plate before serving.',
                ],
                'capture': [
                    'Good, you took the free ingredient. Now plate the position properly.',
                    '{san} wins a {captured_piece}. Do not drop it on the floor next move.',
                    'Material won. Now keep the kitchen clean.',
                ],
                'castle': [
                    'The king is out of the kitchen. Good. Now clean up the rest.',
                    'Good safety. The king is no longer standing in the flames.',
                    'Castled. Fine. Now develop the pieces still sitting in the pantry.',
                ],
                'promotion': [
                    'Promotion served. Do not drop the dish now.',
                    'That pawn became a queen. Now finish the service properly.',
                    'You promoted. Excellent. Do not overcook the win.',
                ],
                'opening': [
                    'Opening has structure. Centre first, develop next, castle before disaster.',
                    '{san} is a decent opening ingredient. Now cook the rest properly.',
                    'Good start. Do not start throwing pawns around like confetti.',
                ],
                'centre': [
                    'Centre control with seasoning. That is more like it.',
                    '{san} takes the middle. Finally, some flavour.',
                    'The centre is the main bench. Own it before attacking.',
                ],
                'development': [
                    'Good, the piece is finally out of the pantry. Put it to work.',
                    '{piece} development is useful. Stop leaving pieces asleep at home.',
                    'Better. Active pieces are ingredients you can actually use.',
                ],
                'endgame': [
                    'Endgame service. One sloppy pawn move and the dish collapses.',
                    'No nonsense now. Count the pawns and keep the king active.',
                    'Endgame cooking. Every tempo matters.',
                ],
                'neutral': [
                    'Fine. Not brilliant, not raw. Keep checking the threats.',
                    '{san} is acceptable. Now look for the forcing moves.',
                    'It will do. But do not get lazy with loose pieces.',
                ],
            },
            'result_lines': {
                'player_win': 'Checkmate. Finally, a finished dish.',
                'bot_win': 'Checkmate. The position was raw and I sent it back.',
                'draw': 'Draw. Edible, but nobody is getting a star for that.',
            },
        },
        {
            'slug': 'einstein',
            'name': 'Einstein',
            'avatar': 'main/bots/einstein.png',
            'style': 'Calculated logic',
            'intro': 'Chess rewards calculation. E = me want checkmate.',
            'chat_lines': [
                'Relatively speaking, your knight is doing nothing.',
                'A simple move can still be genius if it improves the position.',
                'Calculate first. Guessing is not a theory.',
                'Your pieces are cooperating. That is beautiful mathematics.',
                'E equals material advantage squared. Probably.',
                'The shortest variation is often the most elegant.',
                'Your calculation variable is approaching zero. Concerning.',
                'Checkmate is just the universe reaching a conclusion.',
            ],
            'dialogue_lines': {
                'blunder': [
                    'Relatively speaking, that move bent space, time and your evaluation.',
                    '{san} disproves the hypothesis. The opponent response is too strong.',
                    'That calculation collapses. Re-check the forcing moves.',
                ],
                'missed_capture': [
                    'The equation had a better answer: {best_san} wins the {missed_piece}.',
                    'You ignored observable material. The {missed_piece} was available.',
                    '{san} is a weak theory when {best_san} wins material.',
                ],
                'mistake': [
                    'Interesting hypothesis, but the calculation does not fully support it.',
                    '{san} is not absurd, but the evaluation says there is a better line.',
                    'Close, but your formula is missing the opponent reply.',
                ],
                'strong': [
                    'Elegant. A simple move can still be genius.',
                    '{san} is precise. The position now has better logic.',
                    'Good calculation. You improved the equation.',
                ],
                'check': [
                    'Check creates a forcing equation. Now solve the next line.',
                    '{san} limits the king variable. Continue calculating.',
                    'Forcing move found. Now test the reply.',
                ],
                'capture': [
                    'Material acquired. E equals extra piece squared. Probably.',
                    '{san} wins a {captured_piece}. Convert the material into a result.',
                    'Good. The material equation now favours you.',
                ],
                'castle': [
                    'King safety restored. The experiment may continue.',
                    'Castling reduces chaos. Excellent practical physics.',
                    'The king is safer. Now activate the remaining variables.',
                ],
                'promotion': [
                    'A pawn became a queen. Matter has successfully changed state.',
                    'Promotion changes the equation completely. Use the new force.',
                    'The pawn has evolved. Now prove the win.',
                ],
                'opening': [
                    'Opening theory approves. Centre, development and king safety are connected.',
                    '{san} is logical. Continue with development before complications.',
                    'Good opening premise. Do not abandon the formula.',
                ],
                'centre': [
                    'Excellent. Control the centre and the board obeys better physics.',
                    '{san} increases your influence over the board.',
                    'Central control improves the geometry of your pieces.',
                ],
                'development': [
                    'A developed piece has more potential energy. Very useful.',
                    '{piece} development increases your usable force.',
                    'Good activation. Idle pieces contribute nothing to the equation.',
                ],
                'endgame': [
                    'Endgame theory now. Small pawn errors become large conclusions.',
                    'The board is simplified. Calculate king activity and pawn races.',
                    'Endgame precision matters. One tempo can change the result.',
                ],
                'neutral': [
                    'Calculate first. Guessing is not a theory.',
                    '{san} is reasonable. Now verify the opponent reply.',
                    'Stable enough. Search for a clearer plan.',
                ],
            },
            'result_lines': {
                'player_win': 'Checkmate. The equation reached a beautiful conclusion.',
                'bot_win': 'Checkmate. The theory collapsed under calculation.',
                'draw': 'Draw. Equal forces, stable universe.',
            },
        },
    ]



# Tutor definitions describe each coach style and difficulty level.
def get_tutors():
    tutor_levels = [
        ('tutorial', 'Tutorial Tutor', 100, 'New to Chess', 'main/tutors/tutorial-grad-cap.png', 'Learn the rules, piece movement and the objective before playing.', True),
        ('drizzy-drake', 'Drizzy Drake', 400, 'Beginner', None, 'Smooth feedback for basic mistakes, centre control and loose pieces.', False),
        ('lebron', 'LeBron', 600, 'Novice', None, 'Big-picture feedback focused on board vision and smart pressure.', False),
        ('d-mcguire', 'D McGuire', 900, 'Intermediate', None, 'Technical coaching that explains mistakes like debugging a program.', False),
        ('serena-williams', 'Serena Williams', 1200, 'Intermediate II', None, 'Disciplined coaching for pressure, tactics and clean conversion.', False),
        ('gordon-ramsay', 'Gordon Ramsay', 1600, 'Advanced', None, 'Direct mistake feedback with no patience for undercooked moves.', False),
        ('einstein', 'Einstein', 2000, 'Expert', None, 'Calculation-focused tutoring for deeper plans and evaluation swings.', False),
    ]
    themes = {theme['slug']: theme for theme in get_bot_themes()}
    tutors = []

    for number, tutor_data in enumerate(tutor_levels, start=1):
        slug, name, elo, level, avatar, description, tutorial = tutor_data
        theme = themes.get(slug, {})
        intro = (
            'Welcome. I will explain how the pieces move, what checkmate means, and then guide your first game.'
            if tutorial
            else theme.get('intro', 'I will teach you as we play.')
        )
        dialogue_lines = theme.get('dialogue_lines', {})

        if tutorial:
            dialogue_lines = {
                'blunder': [
                    'That move gives away material. Look at what your opponent can capture next.',
                    '{san} makes your position much worse. Before moving, check if any piece is undefended.',
                    'That is a serious mistake. Go back and compare the safe moves first.',
                ],
                'missed_capture': [
                    'You missed the bigger capture. Compare every capture before choosing one.',
                    '{best_san} wins the {missed_piece}. When a piece is free, take it unless there is a trap.',
                    'This is a board vision moment. Look at every capture before playing a quiet move.',
                ],
                'mistake': [
                    'Close, but there is a safer move. Try comparing checks, captures and threats.',
                    '{san} is legal, but it is not the best choice here. Look for the move that wins or protects material.',
                    'Good try. Now go back and ask: what can my opponent capture after this?',
                ],
                'strong': [
                    'Good move. You improved your position without giving anything away.',
                    '{san} is strong because it helps your position and avoids an obvious weakness.',
                    'Nice. That move follows a good chess habit: improve your pieces safely.',
                ],
                'check': [
                    'That is check. The king is under attack and must respond.',
                    '{san} attacks the king. Your opponent must move the king, block, or capture the checking piece.',
                    'Good check. Remember, check is strongest when the next move also improves your position.',
                ],
                'capture': [
                    'Nice capture. You removed an opponent piece from the board.',
                    '{san} wins a {captured_piece}. Now check if your piece can be captured back.',
                    'Good material win. Count the trade before celebrating.',
                ],
                'castle': [
                    'Good castling. That moves your king toward safety and connects your rooks.',
                    'Castling is a strong habit. It protects the king and helps your rook join later.',
                    'Nice. Your king is safer, so now you can focus on active pieces.',
                ],
                'promotion': [
                    'A pawn reached the end and promoted. That is one of chess\'s biggest rewards.',
                    'Promotion usually means choosing a queen because it is the strongest piece.',
                    'Great. A promoted pawn can decide the game, but still avoid stalemate.',
                ],
                'opening': [
                    'Good opening move. You are fighting for the centre and preparing development.',
                    '{san} is a sensible opening. Next, bring out a knight or bishop and keep the king safe.',
                    'That follows opening principles: centre, pieces, king safety.',
                ],
                'centre': [
                    'Good centre control. The middle squares help your pieces move around the board.',
                    '{san} controls important central squares. That gives your pieces more options.',
                    'Nice centre move. The centre is where many attacks begin.',
                ],
                'development': [
                    'Good development. Knights and bishops should enter the game early.',
                    'That {piece} is now active. Developed pieces can attack, defend and control squares.',
                    'Nice. Keep bringing pieces out instead of moving one piece too many times.',
                ],
                'endgame': [
                    'Endgame now. Kings and pawns become much more important.',
                    'In the endgame, your king should help. It is no longer just hiding.',
                    'Count pawn races carefully. One square can decide the result.',
                ],
                'neutral': [
                    'Legal move. Keep asking what your opponent is threatening.',
                    '{san} is legal. Now scan checks, captures and threats before the next move.',
                    'Good to keep playing. Try to improve your least active piece next.',
                ],
            }

        tutors.append(
            {
                'number': f'{number:02}',
                'slug': slug,
                'name': name,
                'avatar': avatar or theme.get('avatar'),
                'elo': elo,
                'level': level,
                'style': theme.get('style', 'Rules and fundamentals') if not tutorial else 'Rules and fundamentals',
                'description': description,
                'intro': intro,
                'dialogue_lines': dialogue_lines,
                'result_lines': theme.get('result_lines', {}),
                'tutorial': tutorial,
                'unlocked': True,
            }
        )

    return tutors


def find_tutor(tutor_slug):
    return next((tutor for tutor in get_tutors() if tutor['slug'] == tutor_slug), None)



# Converts a FEN puzzle into the structure used by the puzzle frontend.
def puzzle_from_fen(title, fen, solution, instruction=''):
    return {
        'title': title,
        'fen': fen,
        'solution': solution,
        'instruction': instruction,
    }


def puzzle_from_san(title, prefix, solution, instruction=''):
    board = chess.Board()

    for move_san in prefix:
        board.push(board.parse_san(move_san))

    fen = board.fen()
    solution_uci = []
    for move_san in solution:
        move = board.parse_san(move_san)
        solution_uci.append(move.uci())
        board.push(move)

    return puzzle_from_fen(title, fen, solution_uci, instruction=instruction)



# Stores multi-move puzzle lines inspired by real tactical training sites.
def puzzle_from_lichess(title, fen, moves, instruction=''):
    board = chess.Board(fen)
    solution_uci = []
    start_fen = fen

    for index, move_uci in enumerate(moves.split()):
        move = chess.Move.from_uci(move_uci)

        if move not in board.legal_moves:
            raise ValueError(f'Invalid Lichess puzzle move {move_uci} from {board.fen()}')

        board.push(move)

        if index == 0:
            start_fen = board.fen()
        else:
            solution_uci.append(move_uci)

    return puzzle_from_fen(title, start_fen, solution_uci, instruction=instruction)


def transform_puzzle_square(square, horizontal=False, mirror=False):
    if horizontal:
        square = chess.square(7 - chess.square_file(square), chess.square_rank(square))

    if mirror:
        square = chess.square_mirror(square)

    return square


def transform_puzzle_move(move_uci, horizontal=False, mirror=False):
    move = chess.Move.from_uci(move_uci)

    return chess.Move(
        transform_puzzle_square(move.from_square, horizontal=horizontal, mirror=mirror),
        transform_puzzle_square(move.to_square, horizontal=horizontal, mirror=mirror),
        promotion=move.promotion,
    ).uci()


def transform_puzzle(puzzle, title, horizontal=False, mirror=False):
    board = chess.Board(puzzle['fen'])

    if horizontal:
        board = board.transform(chess.flip_horizontal)

    if mirror:
        board = board.mirror()

    return puzzle_from_fen(
        title,
        board.fen(),
        [
            transform_puzzle_move(move, horizontal=horizontal, mirror=mirror)
            for move in puzzle['solution']
        ],
        instruction=puzzle.get('instruction', ''),
    )



# Pads each category to five puzzles so every tactic has the same training length.
def build_puzzle_set(*seed_puzzles):
    puzzles = []

    for seed in seed_puzzles:
        puzzles.append(seed)

        if len(puzzles) < 5:
            # Horizontal-only variants keep White to move and avoid invalid side-flipped tactics.
            puzzles.append(transform_puzzle(seed, seed['title'], horizontal=True))

        if len(puzzles) >= 5:
            break

    while seed_puzzles and len(puzzles) < 5:
        puzzles.append(seed_puzzles[-1])

    return [
        {
            **puzzle,
            'title': f'Puzzle {index}',
        }
        for index, puzzle in enumerate(puzzles[:5], start=1)
    ]


def curated_puzzle_set(*puzzles):
    return [
        {
            **puzzle,
            'title': f'Puzzle {index}',
        }
        for index, puzzle in enumerate(puzzles, start=1)
    ]



# Defines the puzzle categories shown on the puzzle training page.
def get_puzzle_categories():
    return [
        {
            'slug': 'opening-moves',
            'number': '01',
            'name': 'Opening Moves',
            'level': 'Beginner',
            'rating': 100,
            'objective': 'Start by controlling the centre and developing pieces.',
            'hint': 'Good openings fight for the centre, develop pieces and prepare king safety.',
            'success': 'Good opening sequence. Your pieces are entering the game with purpose.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san('Puzzle 1', [], ['e4', 'e5', 'Nf3', 'Nc6', 'Bc4']),
                puzzle_from_san('Puzzle 2', [], ['d4', 'Nf6', 'c4', 'e6', 'Nc3']),
                puzzle_from_san('Puzzle 3', [], ['c4', 'e5', 'Nc3', 'Nf6', 'g3']),
                puzzle_from_san('Puzzle 4', [], ['Nf3', 'd5', 'g3', 'Nf6', 'Bg2']),
                puzzle_from_san('Puzzle 5', [], ['e4', 'c5', 'Nf3', 'd6', 'd4']),
            ),
        },
        {
            'slug': 'forks',
            'number': '02',
            'name': 'Forks',
            'level': 'Beginner',
            'rating': 250,
            'objective': 'Attack two important pieces with one move.',
            'hint': 'Knights are excellent at forking because they jump and attack unusual squares.',
            'success': 'Nice fork. You created one move with two threats.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    'r3kbnr/ppp2ppp/2n5/1N6/8/8/PPP2PPP/R1B1KB1R w KQkq - 0 1',
                    ['b5c7', 'e8d8', 'c7a8'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '3r3k/ppp2ppp/8/6N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQ - 0 1',
                    ['g5f7', 'h8g8', 'f7d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '3q3k/ppp2ppp/8/4N3/2B1P3/8/PPPP1PPP/RNBQK2R w KQ - 0 1',
                    ['e5f7', 'h8g8', 'f7d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '3r3k/ppp2ppp/8/4N3/2B5/8/PPPP1PPP/RNBQK2R w KQ - 0 1',
                    ['e5f7', 'h8g8', 'f7d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    'r3kbnr/ppp2ppp/8/1N6/8/8/PPP2PPP/R1B1KB1R w KQkq - 0 1',
                    ['b5c7', 'e8d8', 'c7a8'],
                ),
            ),
        },
        {
            'slug': 'pins',
            'number': '03',
            'name': 'Pins',
            'level': 'Intermediate',
            'rating': 400,
            'objective': 'Pin a piece so it cannot move safely.',
            'hint': 'Line up a bishop, rook or queen with a piece and a more valuable piece behind it.',
            'success': 'Good pin. The front piece is stuck because moving it exposes something valuable.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '4k3/4qppp/8/8/8/5N2/PPP2PPP/4R1K1 w - - 0 1',
                    ['e1e7'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '3k4/3q1ppp/8/8/8/5N2/PPP2PPP/3R2K1 w - - 0 1',
                    ['d1d7'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '2k5/2q2ppp/8/8/8/5N2/PP3PPP/2R3K1 w - - 0 1',
                    ['c1c7'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '6k1/5qpp/8/8/2B5/5N2/PPP2PPP/6K1 w - - 0 1',
                    ['c4f7'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '1k6/1q3ppp/8/8/8/5N2/P1P2PPP/1R4K1 w - - 0 1',
                    ['b1b7'],
                ),
            ),
        },
        {
            'slug': 'skewers',
            'number': '04',
            'name': 'Skewers',
            'level': 'Intermediate',
            'rating': 500,
            'objective': 'Attack the higher value piece first.',
            'hint': 'A skewer forces the valuable piece away, exposing what sits behind it.',
            'success': 'Strong skewer. The first target moves and the piece behind it falls.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '4q3/4kppp/8/8/8/2N2N2/PPP2PPP/R5K1 w - - 0 1',
                    ['a1e1', 'e7d6', 'e1e8'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '3q4/3k1ppp/8/8/8/2N2N2/PPP2PPP/R5K1 w - - 0 1',
                    ['a1d1', 'd7c6', 'd1d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '8/8/8/8/3k3q/2N5/PPP3PP/5RK1 w - - 0 1',
                    ['f1f4', 'd4c5', 'f4h4'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '2q5/2k2ppp/8/8/8/5N2/PP3PPP/R5K1 w - - 0 1',
                    ['a1c1', 'c7b6', 'c1c8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '1q6/1k3ppp/8/8/8/5N2/P4PPP/R5K1 w - - 0 1',
                    ['a1b1', 'b7c6', 'b1b8'],
                ),
            ),
        },
        {
            'slug': 'discovered-attacks',
            'number': '05',
            'name': 'Discovered Attacks',
            'level': 'Intermediate',
            'rating': 650,
            'objective': 'Move one piece to uncover another attack.',
            'hint': 'The moving piece creates one threat while the piece behind it creates another.',
            'success': 'Clean discovered attack. One move opened a line and created a second threat.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    'r2qk2r/ppp2ppp/2n5/4N3/8/8/PPP2PPP/4R1K1 w kq - 0 1',
                    ['e5c6', 'e8f8', 'c6d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    'r2qk2r/ppp2ppp/2n5/4B3/8/2N2N2/PPP2PPP/4R1K1 w kq - 0 1',
                    ['e5c7', 'e8f8', 'c7d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    'r2qk2r/ppp2ppp/8/4N3/8/2N5/PPP2PPP/4R1K1 w kq - 0 1',
                    ['e5c6', 'e8f8', 'c6d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '3qk2r/ppp2ppp/2n5/4N3/8/8/PPP2PPP/4R1K1 w k - 0 1',
                    ['e5c6', 'e8f8', 'c6d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    'r2qk2r/ppp2ppp/8/4B3/8/5N2/PPP2PPP/4R1K1 w kq - 0 1',
                    ['e5c7', 'e8f8', 'c7d8'],
                ),
            ),
        },
        {
            'slug': 'checkmate',
            'number': '06',
            'name': 'Checkmate',
            'level': 'Beginner',
            'rating': 600,
            'objective': 'Find the forcing move that finishes the king.',
            'hint': 'Look for checks first. The final move should leave the king with no legal escape.',
            'success': 'Checkmate. The king has no legal escape.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san(
                    'Puzzle 1',
                    ['e4', 'e5', 'Bc4', 'Nc6', 'Qh5', 'Nf6'],
                    ['Qxf7#'],
                ),
                puzzle_from_san(
                    'Puzzle 2',
                    ['e4', 'e5', 'Nf3', 'd6', 'Bc4', 'Bg4', 'Nc3', 'g6', 'Nxe5', 'Bxd1'],
                    ['Bxf7+', 'Ke7', 'Nd5#'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '6k1/5ppp/8/8/8/2N2N2/PPP2PPP/4R1K1 w - - 0 1',
                    ['e1e8'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '6k1/6pp/8/2B5/8/8/6PP/5RK1 w - - 0 1',
                    ['f1f8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '6rk/6pp/7N/8/8/8/6PP/6K1 w - - 0 1',
                    ['h6f7'],
                ),
            ),
        },
        {
            'slug': 'back-rank',
            'number': '07',
            'name': 'Back Rank Mate',
            'level': 'Advanced',
            'rating': 950,
            'objective': 'Use the trapped king on the back rank.',
            'hint': 'Back rank mates work when the king has no escape squares behind its own pawns.',
            'success': 'Back rank mate. The rook checks and every escape square is blocked.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '6k1/5ppp/8/8/8/2N2N2/PPP2PPP/4R1K1 w - - 0 1',
                    ['e1e8'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '5k2/4pppp/8/8/8/2N2N2/PPP2PPP/3R2K1 w - - 0 1',
                    ['d1d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '7k/6pp/8/8/8/5N2/5PPP/4R1K1 w - - 0 1',
                    ['e1e8'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '6k1/6pp/8/2B5/8/8/6PP/5RK1 w - - 0 1',
                    ['f1f8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1',
                    ['e1e8'],
                ),
            ),
        },
        {
            'slug': 'hanging-pieces',
            'number': '08',
            'name': 'Hanging Pieces',
            'level': 'Beginner',
            'rating': 300,
            'objective': 'Capture a piece that is not defended.',
            'hint': 'Scan every opponent piece and ask whether it can be taken safely.',
            'success': 'Free material. You spotted a piece that was not protected.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '6k1/ppp2ppp/8/q7/2N5/5N2/PPP2PPP/6K1 w - - 0 1',
                    ['c4a5', 'b7b6', 'a5b7'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '6k1/ppp2ppp/8/8/2q5/5N2/PPPN1PPP/6K1 w - - 0 1',
                    ['d2c4'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '6k1/ppp2ppp/8/8/2q5/8/PNP2PPP/6K1 w - - 0 1',
                    ['b2c4'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '6k1/ppp2ppp/8/8/4r3/2N2N2/PPP2PPP/4R1K1 w - - 0 1',
                    ['e1e4'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '6k1/ppp2ppp/8/1b6/8/2N2N2/PPP2PPP/2B3K1 w - - 0 1',
                    ['c3b5'],
                ),
            ),
        },
        {
            'slug': 'defence',
            'number': '09',
            'name': 'Defence',
            'level': 'Intermediate',
            'rating': 700,
            'objective': 'Stop the opponent threat before attacking.',
            'hint': 'Do not only look at your moves. First find what the opponent is threatening.',
            'success': 'Good defence. You removed the danger before starting your own attack.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '6k1/ppp2ppp/8/8/7q/5N2/PPPP1PPP/6K1 w - - 0 1',
                    ['f3h4', 'g7g6', 'h4f3'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '6k1/ppp2ppp/8/8/q7/5N2/PPP2PPP/6K1 w - - 0 1',
                    ['f3d2', 'a4d4', 'd2f3'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '6k1/ppp2ppp/8/8/6q1/5N2/PPPP1PP1/6K1 w - - 0 1',
                    ['c2c3', 'g4d4', 'f3d4'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '6k1/ppp2ppp/8/8/8/5N1q/PPPP1PPP/6K1 w - - 0 1',
                    ['g2g3', 'h3g3', 'f2g3'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '6k1/ppp2ppp/8/8/8/4qN2/PPPP1PPP/6K1 w - - 0 1',
                    ['f3g5', 'e3g5', 'd2d4'],
                ),
            ),
        },
        {
            'slug': 'promotion',
            'number': '10',
            'name': 'Promotion',
            'level': 'Intermediate',
            'rating': 750,
            'objective': 'Promote the pawn into the strongest piece.',
            'hint': 'Choose a queen when the promotion box appears, then keep using the new queen.',
            'success': 'Promotion complete. The new queen becomes the strongest attacker on the board.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', '6k1/4P1pp/8/8/8/5N2/PPP3PP/6K1 w - - 0 1', ['e7e8q']),
                puzzle_from_fen('Puzzle 2', '8/1P4k1/6pp/8/8/5N2/PPP3PP/6K1 w - - 0 1', ['b7b8q', 'g7h7', 'b8f4']),
                puzzle_from_fen('Puzzle 3', '4k3/6P1/6pp/8/8/5N2/PPP3PP/6K1 w - - 0 1', ['g7g8q', 'e8e7', 'g8g6']),
                puzzle_from_fen('Puzzle 4', '8/2P3k1/6pp/8/8/5N2/PPP3PP/6K1 w - - 0 1', ['c7c8q', 'g7h7', 'c8f5']),
                puzzle_from_fen('Puzzle 5', '8/5Pk1/6pp/8/8/5N2/PPP3PP/6K1 w - - 0 1', ['f7f8q', 'g7h7', 'f8f7']),
            ),
        },
        {
            'slug': 'endgames',
            'number': '11',
            'name': 'Endgames',
            'level': 'Advanced',
            'rating': 900,
            'objective': 'Use your king actively in the endgame.',
            'hint': 'In king and pawn endings, opposition and king activity matter more than flashy moves.',
            'success': 'Correct endgame habit. Your king steps forward and supports the pawn.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    '8/4k3/8/4P3/4K3/8/8/8 w - - 0 1',
                    ['e4d5', 'e7d7', 'e5e6'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '8/6k1/8/6P1/6K1/8/8/8 w - - 0 1',
                    ['g4f5', 'g7f7', 'g5g6'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '8/3k4/8/3P4/3K4/8/8/8 w - - 0 1',
                    ['d4c4', 'd7d6', 'c4d4'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    '8/5k2/8/5P2/5K2/8/8/8 w - - 0 1',
                    ['f4g5', 'f7g7', 'f5f6'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '8/2k5/8/2P5/2K5/8/8/8 w - - 0 1',
                    ['c4b5', 'c7b7', 'c5c6'],
                ),
            ),
        },
        {
            'slug': 'double-attacks',
            'number': '12',
            'name': 'Double Attacks',
            'level': 'Intermediate',
            'rating': 800,
            'objective': 'Create two threats at the same time.',
            'hint': 'Checks are powerful double attacks because the opponent must answer the king first.',
            'success': 'That is a double attack. The check wins time to take the second target.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen(
                    'Puzzle 1',
                    'r3kbnr/ppp2ppp/2n5/1N6/8/8/PPP2PPP/R1B1KB1R w KQkq - 0 1',
                    ['b5c7', 'e8d8', 'c7a8'],
                ),
                puzzle_from_fen(
                    'Puzzle 2',
                    '3r3k/ppp2ppp/8/6N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQ - 0 1',
                    ['g5f7', 'h8g8', 'f7d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 3',
                    '4q3/4kppp/8/8/8/2N2N2/PPP2PPP/R5K1 w - - 0 1',
                    ['a1e1', 'e7d6', 'e1e8'],
                ),
                puzzle_from_fen(
                    'Puzzle 4',
                    'r2qk2r/ppp2ppp/2n5/4N3/8/8/PPP2PPP/4R1K1 w kq - 0 1',
                    ['e5c6', 'e8f8', 'c6d8'],
                ),
                puzzle_from_fen(
                    'Puzzle 5',
                    '1q6/1k3ppp/8/8/8/5N2/P4PPP/R5K1 w - - 0 1',
                    ['a1b1', 'b7c6', 'b1b8'],
                ),
            ),
        },
    ]


def get_puzzle_categories():
    # Puzzle lines follow the Lichess structure: the displayed position is the
    # solver's turn, then the user must continue the forced line after replies.
    return [
        {
            'slug': 'opening-moves',
            'number': '01',
            'name': 'Opening Moves',
            'level': 'Beginner',
            'rating': 100,
            'objective': 'Start by controlling the centre and developing pieces.',
            'hint': 'Good openings fight for the centre, develop pieces and prepare king safety.',
            'success': 'Good opening sequence. Your pieces are entering the game with purpose.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san('Puzzle 1', [], ['e4', 'e5', 'Nf3', 'Nc6', 'Bc4'], instruction='Play the Italian-style start: e4, Nf3 and Bc4. Control the centre and develop naturally.'),
                puzzle_from_san('Puzzle 2', [], ['d4', 'd5', 'c4', 'e6', 'Nc3'], instruction='Use the Queen\'s Gambit setup: d4, c4 and Nc3. Claim space before attacking.'),
                puzzle_from_san('Puzzle 3', [], ['c4', 'e5', 'Nc3', 'Nf6', 'g3'], instruction='Play the English setup: c4, Nc3 and g3. Build pressure without rushing.'),
                puzzle_from_san('Puzzle 4', [], ['Nf3', 'd5', 'g3', 'Nf6', 'Bg2'], instruction='Use the Reti setup: Nf3, g3 and Bg2. Develop first, then decide the centre.'),
                puzzle_from_san('Puzzle 5', [], ['e4', 'c5', 'Nf3', 'd6', 'd4'], instruction='Enter the Open Sicilian: e4, Nf3 and d4. Fight for central control straight away.'),
            ),
        },
        {
            'slug': 'forks',
            'number': '02',
            'name': 'Forks',
            'level': 'Beginner',
            'rating': 250,
            'objective': 'Attack two important pieces with one move.',
            'hint': 'Knights are excellent at forking because they jump and attack unusual squares.',
            'success': 'Nice fork. You created one move with two threats.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', 'r1bqkb1r/pppp1ppp/2n2n2/4N3/2B1P3/8/PPPP1PPP/RNBQK2R w KQkq - 0 1', ['e5f7', 'e8e7', 'f7d8'], instruction='Use the knight fork on f7. The knight attacks the queen and rook from one square.'),
                puzzle_from_lichess('Puzzle 2', 'r3r1k1/p4ppp/2p2n2/1p6/3P1qb1/2NQR3/PPB2PP1/R1B3K1 w - - 5 18', 'e3g3 e8e1 g1h2 e1c1 a1c1 f4h6 h2g1 h6c1', instruction='This is a longer fork tactic. Follow the forcing checks and captures until the material win is clear.'),
                puzzle_from_lichess('Puzzle 3', 'Q1b2r1k/p2np2p/5bp1/q7/5P2/4B3/PPP3PP/2KR1B1R w - - 1 17', 'd1d7 a5e1 d7d1 e1e3 c1b1 e3b6', instruction='The queen fork starts after the opponent enters your back rank. Keep following the forced line.'),
                puzzle_from_fen('Puzzle 4', 'r2qk2r/ppp2ppp/2n2n2/8/2B1N3/8/PPPP1PPP/R1BQK2R w KQkq - 0 1', ['e4f6', 'e8f8', 'f6h7'], instruction='Jump with the knight to fork key targets and escape with material.'),
                puzzle_from_fen('Puzzle 5', 'r3k2r/pppq1ppp/2npbn2/4N3/2B1P3/2N5/PPPP1PPP/R1BQ1RK1 w kq - 0 1', ['e5d7', 'e8d7', 'c4e6'], instruction='Use one fork to disturb the king, then find the next forcing move.'),
            ),
        },
        {
            'slug': 'pins',
            'number': '03',
            'name': 'Pins',
            'level': 'Intermediate',
            'rating': 400,
            'objective': 'Pin a piece so it cannot move safely.',
            'hint': 'Line up a bishop, rook or queen with a piece and a more valuable piece behind it.',
            'success': 'Good pin. The front piece is stuck because moving it exposes something valuable.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san('Puzzle 1', ['e4', 'e5', 'Nf3', 'Nc6'], ['Bb5', 'a6', 'Bxc6'], instruction='Create the Ruy Lopez pin, then remove the pinned knight when Black questions the bishop.'),
                puzzle_from_san('Puzzle 2', ['d4', 'Nf6', 'c4', 'e6', 'Nc3'], ['Bb4', 'e3', 'O-O'], instruction='As black, pin the c3 knight and castle before starting centre play.'),
                puzzle_from_san('Puzzle 3', ['e4', 'c5', 'Nf3', 'd6', 'd4', 'cxd4', 'Nxd4', 'Nf6', 'Nc3', 'a6'], ['Bg5', 'e6', 'Qd2'], instruction='Use the Najdorf-style bishop pin, then support queenside castling pressure.'),
                puzzle_from_san('Puzzle 4', ['d4', 'd5', 'c4', 'e6', 'Nc3', 'Nf6'], ['Bg5', 'Be7', 'e3'], instruction='Pin the f6 knight in a Queen\'s Gambit structure and keep developing behind it.'),
                puzzle_from_fen('Puzzle 5', 'r3k2r/ppp1nppp/4b3/8/8/2N2N2/PPP1QPPP/3R2K1 w kq - 0 1', ['d1e1'], instruction='Use the open file. Re1 pins the knight on e7 to the king, so it cannot move freely.'),
            ),
        },
        {
            'slug': 'skewers',
            'number': '04',
            'name': 'Skewers',
            'level': 'Intermediate',
            'rating': 500,
            'objective': 'Attack the higher value piece first.',
            'hint': 'A skewer forces the valuable piece away, exposing what sits behind it.',
            'success': 'Strong skewer. The first target moves and the piece behind it falls.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', '3r2k1/pp5p/6pB/4Pq2/5n2/P7/1PPR4/1K6 w - - 0 36', ['d2d8', 'g8f7', 'd8f8'], instruction='Use the rook check to force the king back, then keep the attack active.'),
                puzzle_from_fen('Puzzle 2', '4r3/2q1kr2/2p2p2/1p2p2Q/pP2p3/4P3/1PP4R/5RK1 w - - 0 34', ['h5f7', 'e7f7', 'h2h7'], instruction='Check first with the queen, then use the rook to continue the forcing line.'),
                puzzle_from_fen('Puzzle 3', '4q1k1/5ppp/8/8/8/2N2N2/PPPP1PPP/4R1K1 w - - 0 1', ['e1e8'], instruction='Skewer the king and queen along the open file.'),
                puzzle_from_fen('Puzzle 4', '2q3k1/5ppp/8/8/8/5N2/PP1P1PPP/R5K1 w - - 0 1', ['b2b4', 'c8c2', 'a2a4'], instruction='Create counterplay while the queen is exposed on the back rank.'),
                puzzle_from_fen('Puzzle 5', '6kq/6p1/8/8/8/8/5PPP/4R1K1 w - - 0 1', ['e1e8', 'g8h7', 'e8h8'], instruction='Use Re8+ to skewer the king and queen. When the king moves, take the queen on h8.'),
            ),
        },
        {
            'slug': 'discovered-attacks',
            'number': '05',
            'name': 'Discovered Attacks',
            'level': 'Intermediate',
            'rating': 650,
            'objective': 'Move one piece to uncover another attack.',
            'hint': 'The moving piece creates one threat while the piece behind it creates another.',
            'success': 'Clean discovered attack. One move opened a line and created a second threat.',
            'puzzles': curated_puzzle_set(
                puzzle_from_lichess('Puzzle 1', '1k1r4/pp3pp1/2p1p3/4b3/P3n1P1/8/KPP2PN1/3rBR1R b - - 2 31', 'b8c7 e1a5 b7b6 f1d1', instruction='Move the bishop with tempo and uncover pressure from the rook.'),
                puzzle_from_fen('Puzzle 2', 'r2qk2r/ppp2ppp/2n5/4N3/8/2N5/PPP2PPP/4R1K1 w kq - 0 1', ['e5c6', 'e8f8', 'c6d8'], instruction='Move the knight off the e-file to uncover the rook attack, then win the queen.'),
                puzzle_from_fen('Puzzle 3', 'r2qk2r/ppp2ppp/8/4B3/8/5N2/PPP2PPP/4R1K1 w kq - 0 1', ['e5c7', 'e8f8', 'c7d8'], instruction='The bishop moves with check and uncovers the rook line.'),
                puzzle_from_fen('Puzzle 4', 'r2q1rk1/ppp2ppp/2n2n2/4N3/2B1P3/2N5/PPPP1PPP/R1BQR1K1 w - - 0 1', ['e5f3', 'c6d4', 'f3d4'], instruction='Move the knight, then use the follow-up capture to keep the attack alive.'),
                puzzle_from_fen('Puzzle 5', 'r3k2r/ppp2ppp/2n2n2/4B3/8/2N2N2/PPPP1PPP/R2QR1K1 w kq - 0 1', ['e5f6', 'e8d7', 'f6h4'], instruction='Move the bishop with check, then keep pressure after the king reply.'),
            ),
        },
        {
            'slug': 'checkmate',
            'number': '06',
            'name': 'Checkmate',
            'level': 'Beginner',
            'rating': 600,
            'objective': 'Find the forcing move that finishes the king.',
            'hint': 'Look for checks first. The final move should leave the king with no legal escape.',
            'success': 'Checkmate. The king has no legal escape.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san('Puzzle 1', ['e4', 'e5', 'Bc4', 'Nc6', 'Qh5', 'Nf6'], ['Qxf7#'], instruction='The queen and bishop both attack f7. Finish the Scholar pattern.'),
                puzzle_from_san('Puzzle 2', ['e4', 'e5', 'Nf3', 'd6', 'Bc4', 'Bg4', 'Nc3', 'g6', 'Nxe5', 'Bxd1'], ['Bxf7+', 'Ke7', 'Nd5#'], instruction='Use Legal\'s mate: sacrifice the queen, then coordinate bishop and knight.'),
                puzzle_from_lichess('Puzzle 3', 'q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17', 'e8d7 a2e6 d7d8 f7f8', instruction='After the king steps forward, find the forcing mate in two.'),
                puzzle_from_san('Puzzle 4', ['f3', 'e5', 'g4'], ['Qh4#'], instruction='As black, punish the opened diagonal to the king.'),
                puzzle_from_fen('Puzzle 5', '6k1/5ppp/8/8/8/2N2N2/PPPQ1PPP/4R1K1 w - - 0 1', ['e1e8'], instruction='Use the rook to deliver a back-rank mate with the escape squares blocked.'),
            ),
        },
        {
            'slug': 'back-rank',
            'number': '07',
            'name': 'Back Rank Mate',
            'level': 'Advanced',
            'rating': 950,
            'objective': 'Use the trapped king on the back rank.',
            'hint': 'Back rank mates work when the king has no escape squares behind its own pawns.',
            'success': 'Back rank mate. The rook checks and every escape square is blocked.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', '6k1/5ppp/8/8/8/2N2N2/PPPQ1PPP/4R1K1 w - - 0 1', ['e1e8'], instruction='The pawns trap the king. Use the rook on the open file.'),
                puzzle_from_fen('Puzzle 2', '5k2/4pppp/8/8/8/2N2N2/PPP2PPP/3R2K1 w - - 0 1', ['d1d8'], instruction='Same idea, different file: the king cannot run from the back rank.'),
                puzzle_from_fen('Puzzle 3', '7k/6pp/8/8/8/5N2/5PPP/4R1K1 w - - 0 1', ['e1e8'], instruction='Force mate on the eighth rank while the king is boxed in.'),
                puzzle_from_fen('Puzzle 4', '6k1/6pp/8/2B5/8/8/6PP/5RK1 w - - 0 1', ['f1f8'], instruction='The bishop protects the rook entry. Finish on f8.'),
                puzzle_from_fen('Puzzle 5', '6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1', ['e1e8'], instruction='Recognise the basic back-rank pattern: rook check, no escape.'),
            ),
        },
        {
            'slug': 'hanging-pieces',
            'number': '08',
            'name': 'Hanging Pieces',
            'level': 'Beginner',
            'rating': 300,
            'objective': 'Capture a piece that is not defended.',
            'hint': 'Scan every opponent piece and ask whether it can be taken safely.',
            'success': 'Free material. You spotted a piece that was not protected.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', 'rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3', ['f3h4'], instruction='Black moved the queen too early. Capture the loose queen with the knight.'),
                puzzle_from_fen('Puzzle 2', 'r3k2r/ppp2ppp/2n2n2/8/2q5/2N2N2/PPP2PPP/R2QKB1R w KQkq - 0 1', ['f1c4'], instruction='The queen wandered onto an undefended square. Capture it safely.'),
                puzzle_from_fen('Puzzle 3', 'r3k2r/ppp2ppp/2n2n2/8/2b5/2N2N2/PPP2PPP/R2QKB1R w KQkq - 0 1', ['f1c4'], instruction='This time the loose piece is a bishop. Check whether it is defended before taking.'),
                puzzle_from_fen('Puzzle 4', 'r3k2r/ppp2ppp/2n2n2/8/4r3/2N2N2/PPPP1PPP/R3R1K1 w kq - 0 1', ['e1e4'], instruction='A rook has landed undefended in the centre. Remove it.'),
                puzzle_from_fen('Puzzle 5', 'r3k2r/ppp2ppp/2n2n2/1b6/8/2N2N2/PPPB1PPP/R2QK2R w KQkq - 0 1', ['c3b5'], instruction='The knight can capture the loose bishop without losing material.'),
            ),
        },
        {
            'slug': 'defence',
            'number': '09',
            'name': 'Defence',
            'level': 'Intermediate',
            'rating': 700,
            'objective': 'Stop the opponent threat before attacking.',
            'hint': 'Do not only look at your moves. First find what the opponent is threatening.',
            'success': 'Good defence. You removed the danger before starting your own attack.',
            'puzzles': curated_puzzle_set(
                puzzle_from_san('Puzzle 1', ['e4', 'e5', 'Qh5', 'Nc6', 'Bc4'], ['g6', 'Qf3', 'Nf6'], instruction='Black must stop the queen-and-bishop mate threat on f7 before doing anything fancy.'),
                puzzle_from_fen('Puzzle 2', '6k1/5ppp/8/8/8/8/5PPP/4R1K1 b - - 0 1', ['g7g6'], instruction='Create an escape square so the back-rank mate threat no longer works.'),
                puzzle_from_fen('Puzzle 3', 'r4rk1/ppp2ppp/2n2n2/8/7q/5N2/PPPP1PPP/R2Q1RK1 w - - 0 1', ['f3h4'], instruction='The queen is loose and dangerous. Capture the attacking queen with tempo.'),
                puzzle_from_fen('Puzzle 4', 'r4rk1/ppp2ppp/2n2n2/8/8/5N1q/PPPP1PPP/R2Q1RK1 w - - 0 1', ['g2h3'], instruction='Do not ignore threats around your king. The pawn can capture the queen on h3.'),
                puzzle_from_fen('Puzzle 5', 'r4rk1/ppp2ppp/2n2n2/8/8/4qN2/PPPP1PPP/R2Q1RK1 w - - 0 1', ['f2e3', 'f8d8', 'd1e1'], instruction='Defend by capturing the queen, then centralise and finish development.'),
            ),
        },
        {
            'slug': 'promotion',
            'number': '10',
            'name': 'Promotion',
            'level': 'Intermediate',
            'rating': 750,
            'objective': 'Promote the pawn into the strongest piece.',
            'hint': 'Choose a queen when the promotion box appears, then keep using the new queen.',
            'success': 'Promotion complete. The new queen becomes the strongest attacker on the board.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', '6k1/P4ppp/8/8/8/2N5/5PPP/4R1K1 w - - 0 1', ['a7a8q'], instruction='Promote to a queen with check. The extra pieces make the king boxed in.'),
                puzzle_from_fen('Puzzle 2', '2r3k1/1P3ppp/8/8/8/2N5/5PPP/6K1 w - - 0 1', ['b7c8q'], instruction='The pawn can capture on c8 and promote. Choose queen, not a weaker piece.'),
                puzzle_from_fen('Puzzle 3', '4r1k1/5Ppp/8/8/8/2N5/5PPP/6K1 w - - 0 1', ['f7e8q'], instruction='Capture the rook while promoting. That turns a pawn into a decisive queen.'),
                puzzle_from_fen('Puzzle 4', '6k1/2P2ppp/8/8/8/2N5/5PPP/6K1 w - - 0 1', ['c7c8q'], instruction='Promote on c8. The queen is strongest and immediately checks the king.'),
                puzzle_from_fen('Puzzle 5', '5r1k/6Pp/8/8/8/2N5/5PPP/6K1 w - - 0 1', ['g7f8q'], instruction='Use promotion as a capture. Take the rook and promote to a queen.'),
            ),
        },
        {
            'slug': 'endgames',
            'number': '11',
            'name': 'Endgames',
            'level': 'Advanced',
            'rating': 900,
            'objective': 'Use your king actively in the endgame.',
            'hint': 'In king and pawn endings, opposition and king activity matter more than flashy moves.',
            'success': 'Correct endgame habit. Your king steps forward and supports the pawn.',
            'puzzles': curated_puzzle_set(
                puzzle_from_fen('Puzzle 1', '8/4k3/6p1/3KP2p/7P/6P1/8/8 w - - 0 1', ['e5e6', 'e7f6', 'd5d6'], instruction='Push the passed pawn only when your king is close enough to support it.'),
                puzzle_from_fen('Puzzle 2', '8/6k1/6p1/5KP1/6P1/7p/7P/8 w - - 0 1', ['f5e6', 'g7g8', 'e6f6'], instruction='Use the king first. Opposition wins the race before the pawn moves.'),
                puzzle_from_fen('Puzzle 3', '8/3k4/8/2PP4/2K5/8/8/8 w - - 0 1', ['c4b5', 'd7c7', 'c5c6'], instruction='Improve the king, force the enemy king back, then push the passer.'),
                puzzle_from_fen('Puzzle 4', '8/5k2/5p2/5P2/5K2/8/8/8 w - - 0 1', ['f4g4', 'f7g7', 'g4h5'], instruction='Use king opposition to win access to the pawn instead of rushing.'),
                puzzle_from_fen('Puzzle 5', '8/2k5/2p5/2P5/2K5/8/8/8 w - - 0 1', ['c4d4', 'c7d7', 'd4e4'], instruction='Walk around with the king. In endgames, one tempo can decide the result.'),
            ),
        },
        {
            'slug': 'double-attacks',
            'number': '12',
            'name': 'Double Attacks',
            'level': 'Intermediate',
            'rating': 800,
            'objective': 'Create two threats at the same time.',
            'hint': 'Checks are powerful double attacks because the opponent must answer the king first.',
            'success': 'That is a double attack. The check wins time to take the second target.',
            'puzzles': curated_puzzle_set(
                puzzle_from_lichess('Puzzle 1', 'r3r1k1/p4ppp/2p2n2/1p6/3P1qb1/2NQR3/PPB2PP1/R1B3K1 w - - 5 18', 'e3g3 e8e1 g1h2 e1c1 a1c1 f4h6 h2g1 h6c1', instruction='A forcing move creates more than one problem. Follow the checks and captures.'),
                puzzle_from_fen('Puzzle 2', 'r1bqkb1r/pppp1ppp/2n2n2/4N3/2B1P3/8/PPPP1PPP/RNBQK2R w KQkq - 0 1', ['e5f7', 'e8e7', 'f7d8'], instruction='The knight attacks two valuable pieces at once.'),
                puzzle_from_fen('Puzzle 3', '4q3/4kppp/8/8/8/2N2N2/PPP2PPP/R5K1 w - - 0 1', ['a1e1', 'e7d6', 'e1e8'], instruction='Use the rook to create a checking attack and win the queen.'),
                puzzle_from_lichess('Puzzle 4', 'Q1b2r1k/p2np2p/5bp1/q7/5P2/4B3/PPP3PP/2KR1B1R w - - 1 17', 'd1d7 a5e1 d7d1 e1e3 c1b1 e3b6', instruction='A queen move creates threats on both the king and loose material.'),
                puzzle_from_fen('Puzzle 5', 'r2qk2r/ppp2ppp/2n5/4N3/8/2N5/PPP2PPP/4R1K1 w kq - 0 1', ['e5c6', 'e8f8', 'c6d8'], instruction='The discovered attack also creates a double attack on the queen.'),
            ),
        },
    ]


def find_puzzle_category(category_slug):
    return next((puzzle for puzzle in get_puzzle_categories() if puzzle['slug'] == category_slug), None)


def puzzle_board_squares(fen):
    board = chess.Board(fen)
    squares = []

    for rank in range(7, -1, -1):
        for file_index in range(8):
            square_index = chess.square(file_index, rank)
            square_name = chess.square_name(square_index)
            piece = board.piece_at(square_index)
            piece_code = None

            if piece:
                piece_code = ('w' if piece.color == chess.WHITE else 'b') + piece.symbol().lower()

            squares.append(
                {
                    'square': square_name,
                    'piece': piece_code,
                    'light': (rank + file_index) % 2 == 1,
                    'rank_label': str(rank + 1) if file_index == 0 else '',
                    'file_label': chr(97 + file_index) if rank == 0 else '',
                }
            )

    return squares



# Sends one puzzle position, line and metadata to JavaScript.
def serialize_puzzle(puzzle, category):
    board = chess.Board(puzzle['fen'])
    temp_board = board.copy()
    solution_san = []
    steps = []

    for move_index, move_uci in enumerate(puzzle['solution']):
        move = chess.Move.from_uci(move_uci)
        move_san = temp_board.san(move)
        solution_san.append(move_san)

        if move_index % 2 == 0:
            steps.append(
                {
                    'number': (move_index // 2) + 1,
                    'legal_moves': [legal_move.uci() for legal_move in temp_board.legal_moves],
                    'expected': move_uci,
                    'expected_san': move_san,
                }
            )
        else:
            steps[-1]['reply'] = move_uci
            steps[-1]['reply_san'] = move_san

        temp_board.push(move)

    pieces = []
    for square, piece in board.piece_map().items():
        pieces.append(
            {
                'square': chess.square_name(square),
                'piece': ('w' if piece.color == chess.WHITE else 'b') + piece.symbol().lower(),
            }
        )

    return {
        'title': puzzle['title'],
        'name': category['name'],
        'number': category['number'],
        'rating': category['rating'],
        'level': category['level'],
        'fen': puzzle['fen'],
        'pieces': pieces,
        'solution': puzzle['solution'],
        'steps': steps,
        'solution_san': solution_san,
        'objective': category['objective'],
        'hint': category['hint'],
        'success': category['success'],
        'instruction': puzzle.get('instruction', ''),
        'turn': 'white' if board.turn == chess.WHITE else 'black',
    }



# Adds rating progress to a puzzle category before rendering it.
def serialize_puzzle_category(category, puzzle_rating=100, rated_attempts=None):
    rated_attempts = rated_attempts or []

    return {
        'slug': category['slug'],
        'number': category['number'],
        'name': category['name'],
        'rating': category['rating'],
        'player_rating': puzzle_rating,
        'rated_attempts': rated_attempts,
        'rated_completed_count': len(rated_attempts),
        'level': category['level'],
        'objective': category['objective'],
        'hint': category['hint'],
        'success': category['success'],
        'puzzles': [
            serialize_puzzle(puzzle, category)
            for puzzle in category['puzzles']
        ],
    }



# Lesson data drives the interactive new-to-chess tutor walkthrough.
def tutorial_lessons():
    return [
        {
            'title': 'Objective',
            'text': 'The goal is checkmate: attack the king so it has no legal escape.',
        },
        {
            'title': 'Pawn',
            'text': 'Pawns move forward one square, capture diagonally, and can promote on the final rank.',
        },
        {
            'title': 'Knight',
            'text': 'Knights move in an L shape and can jump over other pieces.',
        },
        {
            'title': 'Bishop',
            'text': 'Bishops move diagonally across any number of open squares.',
        },
        {
            'title': 'Rook',
            'text': 'Rooks move horizontally or vertically across open files and ranks.',
        },
        {
            'title': 'Queen',
            'text': 'The queen combines rook and bishop movement, making it the most powerful piece.',
        },
        {
            'title': 'King',
            'text': 'The king moves one square at a time and must never stay in check.',
        },
    ]



# Reads ladder progress from the session and creates it if it does not exist yet.
def get_progress(request):
    progress = request.session.get(PROGRESS_SESSION_KEY)

    if not progress:
        progress = {
            'player_elo': STARTING_PLAYER_ELO,
            'defeated_bots': [],
        }
        request.session[PROGRESS_SESSION_KEY] = progress
        request.session.modified = True

    defeated_bots = progress.setdefault('defeated_bots', [])
    player_elo = calculate_player_elo(defeated_bots)

    if progress.get('player_elo') != player_elo:
        progress['player_elo'] = player_elo
        request.session[PROGRESS_SESSION_KEY] = progress
        request.session.modified = True

    return progress



# Updates progress when a ladder bot is beaten and unlocks the next level.
def record_bot_win(request, bot):
    progress = get_progress(request)
    defeated_bots = progress.setdefault('defeated_bots', [])

    if bot['slug'] not in defeated_bots:
        defeated_bots.append(bot['slug'])
        progress['player_elo'] = calculate_player_elo(defeated_bots)
        request.session[PROGRESS_SESSION_KEY] = progress
        request.session.modified = True

    return progress


def find_bot(bot_slug, progress=None):
    return next((bot for bot in get_bot_ladder(progress) if bot['slug'] == bot_slug), None)


def find_theme(theme_slug):
    return next((theme for theme in get_bot_themes() if theme['slug'] == theme_slug), None)


def get_user_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def clamp_score(value):
    return max(0, min(100, int(round(value))))



# Creates SVG radar-chart points from the player's skill scores.
def dashboard_radar_points(skill_rows):
    centre_x = 170
    centre_y = 170
    radius = 122
    axis_radius = 136
    label_radius = 158
    radar_rows = []
    polygon_points = []

    for index, skill in enumerate(skill_rows):
        angle = (-math.pi / 2) + ((2 * math.pi * index) / len(skill_rows))
        value_radius = radius * (skill['value'] / 100)
        polygon_points.append(
            f'{centre_x + math.cos(angle) * value_radius:.1f},{centre_y + math.sin(angle) * value_radius:.1f}'
        )
        radar_rows.append(
            {
                **skill,
                'axis_x': f'{centre_x + math.cos(angle) * axis_radius:.1f}',
                'axis_y': f'{centre_y + math.sin(angle) * axis_radius:.1f}',
                'label_x': f'{centre_x + math.cos(angle) * label_radius:.1f}',
                'label_y': f'{centre_y + math.sin(angle) * label_radius:.1f}',
                'anchor': 'middle',
            }
        )

    return radar_rows, ' '.join(polygon_points)


def dashboard_ring_points(scale):
    centre_x = 170
    centre_y = 170
    radius = 122 * scale
    points = []

    for index in range(8):
        angle = (-math.pi / 2) + ((2 * math.pi * index) / 8)
        points.append(f'{centre_x + math.cos(angle) * radius:.1f},{centre_y + math.sin(angle) * radius:.1f}')

    return ' '.join(points)


@login_required

# Dashboard combines bot, tutor and puzzle data into one progress view.
def dashboard_view(request):
    profile = get_user_profile(request.user)
    progress = get_progress(request)
    bots = get_bot_ladder(progress)
    defeated_bots = [bot for bot in bots if bot['status'] == 'Defeated']
    active_bot = next((bot for bot in bots if bot['active']), bots[-1])

    puzzle_categories = get_puzzle_categories()
    category_lookup = {category['slug']: category for category in puzzle_categories}
    total_puzzles = sum(len(category['puzzles']) for category in puzzle_categories)
    attempts = list(PuzzleRatingAttempt.objects.filter(user=request.user).order_by('-created_at'))
    solved_puzzles = len(attempts)
    perfect_puzzles = sum(1 for attempt in attempts if attempt.perfect)
    replay_puzzles = max(total_puzzles - perfect_puzzles, 0)

    bot_wins = len(defeated_bots)
    recorded_wins = bot_wins + perfect_puzzles
    recorded_losses = max(solved_puzzles - perfect_puzzles, 0)
    recorded_total = recorded_wins + recorded_losses
    win_rate = round((recorded_wins / recorded_total) * 100) if recorded_total else 0
    puzzle_completion = round((solved_puzzles / total_puzzles) * 100) if total_puzzles else 0

    active_bot_game = request.session.get(GAME_SESSION_KEY) or {}
    active_tutor_game = request.session.get(TUTOR_SESSION_KEY) or {}
    analysed_moves = [
        move.get('analysis', {})
        for game in (active_bot_game, active_tutor_game)
        for move in game.get('moves', [])
        if move.get('side') == 'player' and move.get('analysis')
    ]
    move_categories = [analysis.get('category', 'neutral') for analysis in analysed_moves]
    strong_moves = move_categories.count('strong') + move_categories.count('centre') + move_categories.count('development')
    mistakes = move_categories.count('mistake') + move_categories.count('blunder')
    captures = sum(1 for analysis in analysed_moves if analysis.get('is_capture'))
    checks = sum(1 for analysis in analysed_moves if analysis.get('is_check'))

    category_attempts = {}
    category_perfect = {}

    for attempt in attempts:
        category_attempts[attempt.category_slug] = category_attempts.get(attempt.category_slug, 0) + 1
        if attempt.perfect:
            category_perfect[attempt.category_slug] = category_perfect.get(attempt.category_slug, 0) + 1

    def attempt_score(slugs, base=42, per_attempt=8, per_perfect=6):
        attempted = sum(category_attempts.get(slug, 0) for slug in slugs)
        perfect = sum(category_perfect.get(slug, 0) for slug in slugs)
        return clamp_score(base + (attempted * per_attempt) + (perfect * per_perfect) + (bot_wins * 3))

    skill_rows = [
        {
            'name': 'Openings',
            'short': 'Open',
            'value': attempt_score(['opening-moves'], base=38, per_attempt=11),
        },
        {
            'name': 'Tactics',
            'short': 'Tactics',
            'value': clamp_score(
                attempt_score(
                    ['checkmate', 'forks', 'pins', 'skewers', 'discovered-attacks', 'double-attacks', 'hanging-pieces'],
                    base=40,
                    per_attempt=4,
                    per_perfect=5,
                )
                + captures
                + checks
            ),
        },
        {
            'name': 'Endgames',
            'short': 'End',
            'value': attempt_score(['endgames', 'promotion'], base=36, per_attempt=8),
        },
        {
            'name': 'Defence',
            'short': 'Def',
            'value': clamp_score(attempt_score(['defence'], base=42, per_attempt=10) - (mistakes * 5)),
        },
        {
            'name': 'Board Vision',
            'short': 'Vision',
            'value': clamp_score(44 + (perfect_puzzles * 3) + (strong_moves * 4) + (captures * 3) - (mistakes * 4)),
        },
        {
            'name': 'Puzzle Solving',
            'short': 'Puzzles',
            'value': clamp_score(35 + (profile.puzzle_rating / 18) + (perfect_puzzles * 4)),
        },
        {
            'name': 'Accuracy',
            'short': 'Acc',
            'value': clamp_score(50 + (win_rate / 2) + (strong_moves * 3) - (mistakes * 6)),
        },
        {
            'name': 'Aggression',
            'short': 'Attack',
            'value': clamp_score(38 + (checks * 8) + (captures * 5) + (bot_wins * 5)),
        },
    ]
    radar_rows, radar_polygon = dashboard_radar_points(skill_rows)

    recent_attempts = []
    for attempt in attempts[:5]:
        category = category_lookup.get(attempt.category_slug, {'name': attempt.category_slug.replace('-', ' ').title()})
        recent_attempts.append(
            {
                'title': f'{category["name"]} puzzle {attempt.puzzle_index + 1}',
                'result': 'Perfect' if attempt.perfect else 'Review',
                'delta': attempt.delta,
            }
        )

    recent_activity = [
        {
            'title': 'Bot ladder',
            'body': f'{bot_wins} of {len(bots)} bots defeated. Current target: {active_bot["name"]}.',
            'meta': f'{progress["player_elo"]} ELO',
        },
        {
            'title': 'Puzzle training',
            'body': f'{solved_puzzles} of {total_puzzles} rated puzzles attempted.',
            'meta': f'{profile.puzzle_rating} puzzle rating',
        },
        {
            'title': 'Tutor coach',
            'body': 'Active guided training session found.' if active_tutor_game else 'Start a tutor game to build move feedback history.',
            'meta': 'Live feedback',
        },
    ]

    if recent_attempts:
        recent_activity.extend(
            {
                'title': attempt['title'],
                'body': attempt['result'],
                'meta': f'{attempt["delta"]:+d} rating' if attempt['delta'] else 'recorded',
            }
            for attempt in recent_attempts[:2]
        )

    dashboard_stats = [
        {
            'label': 'Current ELO',
            'value': progress['player_elo'],
            'tone': 'green',
            'caption': f'{bot_wins}/{len(bots)} ladder bots defeated',
        },
        {
            'label': 'Puzzle Rating',
            'value': profile.puzzle_rating,
            'tone': 'gold',
            'caption': f'{puzzle_completion}% of rated puzzle set attempted',
        },
        {
            'label': 'Win Rate',
            'value': f'{win_rate}%',
            'tone': 'split',
            'caption': f'{recorded_wins} wins / {recorded_losses} losses or reviews',
        },
        {
            'label': 'To Review',
            'value': replay_puzzles,
            'tone': 'blue',
            'caption': 'unsolved or imperfect puzzles remaining',
        },
    ]

    mode_cards = [
        {
            'name': 'Bot Ladder',
            'value': f'{bot_wins}/{len(bots)}',
            'label': 'bots defeated',
            'href': reverse('play'),
        },
        {
            'name': 'Tutor Coach',
            'value': len([move for move in active_tutor_game.get('moves', []) if move.get('side') == 'player']),
            'label': 'moves in active tutor game',
            'href': reverse('tutors'),
        },
        {
            'name': 'Puzzles',
            'value': f'{perfect_puzzles}/{total_puzzles}',
            'label': 'perfect rated solves',
            'href': reverse('puzzles'),
        },
    ]

    return render(
        request,
        'main/dashboard.html',
        {
            'profile': profile,
            'dashboard_stats': dashboard_stats,
            'mode_cards': mode_cards,
            'skill_rows': skill_rows,
            'radar_rows': radar_rows,
            'radar_polygon': radar_polygon,
            'radar_rings': [dashboard_ring_points(scale) for scale in (0.25, 0.5, 0.75, 1)],
            'recent_activity': recent_activity,
            'active_bot': active_bot,
            'bots_defeated': bot_wins,
            'total_bots': len(bots),
            'tracked_results': recorded_total,
            'player_elo': progress['player_elo'],
            'puzzle_rating': profile.puzzle_rating,
        },
    )



# Reuses the current bot game from the session or starts a new one when needed.
def get_or_create_game(request, bot, theme):
    game = request.session.get(GAME_SESSION_KEY)

    if (
        request.GET.get('new') == '1'
        or not game
        or game.get('bot_slug') != bot['slug']
        or game.get('theme_slug') != theme['slug']
    ):
        game = create_game(bot['slug'], theme['slug'], theme['intro'], theme['name'])
        request.session[GAME_SESSION_KEY] = game
        request.session.modified = True

    return game



# Keeps the active tutor game alive between page reloads.
def get_or_create_tutor_game(request, tutor):
    game = request.session.get(TUTOR_SESSION_KEY)

    if (
        request.GET.get('new') == '1'
        or not game
        or game.get('bot_slug') != tutor['slug']
        or game.get('mode') != 'tutor'
    ):
        game = create_tutor_game(tutor)
        request.session[TUTOR_SESSION_KEY] = game
        request.session.modified = True

    return game



# Homepage introduces the core promise: play, receive feedback and improve faster.
def home(request):
    return render(request, 'main/home.html')


@login_required

# Settings handles profile picture uploads and password changes in one place.
def account_settings_view(request):
    profile = get_user_profile(request.user)
    avatar_form = ProfilePictureForm(instance=profile)
    password_form = PasswordChangeForm(request.user)

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        if form_type == 'avatar':
            avatar_form = ProfilePictureForm(request.POST, request.FILES, instance=profile)

            if avatar_form.is_valid():
                avatar_form.save()
                messages.success(request, 'Profile picture updated.')
                return redirect('account_settings')

            messages.error(request, 'Profile picture could not be updated.')

        elif form_type == 'password':
            password_form = PasswordChangeForm(request.user, request.POST)

            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password updated.')
                return redirect('account_settings')

            messages.error(request, 'Password could not be updated.')

    return render(
        request,
        'main/account_settings.html',
        {
            'profile': profile,
            'avatar_form': avatar_form,
            'password_form': password_form,
        },
    )


@login_required

# Play page shows the user's ladder progress and currently available bot.
def play_view(request):
    progress = get_progress(request)
    bots = get_bot_ladder(progress)

    return render(
        request,
        'main/play.html',
        {
            'bots': bots,
            'selected_bot': next((bot for bot in bots if bot['active']), bots[0]),
            'player_elo': progress['player_elo'],
            'bots_defeated': len(progress.get('defeated_bots', [])),
            'total_bots': len(bots),
        },
    )


@login_required

# Puzzle page lists tactic categories and rated progress.
def puzzles_view(request):
    profile = get_user_profile(request.user)
    attempt_rows = PuzzleRatingAttempt.objects.filter(user=request.user).values_list('category_slug', 'puzzle_index')
    attempts_by_category = {}

    for category_slug, puzzle_index in attempt_rows:
        attempts_by_category.setdefault(category_slug, set()).add(puzzle_index)

    categories = []

    for category in get_puzzle_categories():
        preview_puzzle = category['puzzles'][0]
        puzzle_state = serialize_puzzle(preview_puzzle, category)
        rated_completed = len(attempts_by_category.get(category['slug'], set()))
        categories.append(
            {
                **category,
                'preview_squares': puzzle_board_squares(preview_puzzle['fen']),
                'solution_san': puzzle_state['solution_san'][0],
                'puzzle_count': len(category['puzzles']),
                'rated_completed': rated_completed,
                'rated_available': rated_completed < len(category['puzzles']),
            }
        )

    total_puzzles = sum(category['puzzle_count'] for category in categories)

    return render(
        request,
        'main/puzzles.html',
        {
            'categories': categories,
            'puzzle_rating': profile.puzzle_rating,
            'total_categories': len(categories),
            'total_puzzles': total_puzzles,
        },
    )


@login_required

# Puzzle detail view loads the selected category's interactive board.
def puzzle_detail_view(request, category_slug):
    puzzle = find_puzzle_category(category_slug)

    if puzzle is None:
        return redirect('puzzles')

    profile = get_user_profile(request.user)
    rated_attempts = list(
        PuzzleRatingAttempt.objects.filter(user=request.user, category_slug=puzzle['slug'])
        .values_list('puzzle_index', flat=True)
    )

    return render(
        request,
        'main/puzzle_detail.html',
        {
            'puzzle': puzzle,
            'puzzle_state': serialize_puzzle_category(puzzle, profile.puzzle_rating, rated_attempts=rated_attempts),
            'puzzle_count': len(puzzle['puzzles']),
            'categories': get_puzzle_categories(),
            'puzzle_rating': profile.puzzle_rating,
        },
    )


@login_required
@require_POST

# Rated result endpoint awards puzzle ELO once per puzzle.
def puzzle_rated_result_view(request, category_slug):
    puzzle = find_puzzle_category(category_slug)

    if puzzle is None:
        return JsonResponse({'ok': False, 'error': 'This puzzle category is not available.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid rated puzzle result.'}, status=400)

    perfect = bool(payload.get('perfect'))
    puzzle_index = payload.get('puzzle_index')

    try:
        puzzle_index = int(puzzle_index)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid puzzle index.'}, status=400)

    if puzzle_index < 0 or puzzle_index >= len(puzzle['puzzles']):
        return JsonResponse({'ok': False, 'error': 'Puzzle index is out of range.'}, status=400)

    profile = get_user_profile(request.user)
    delta = 10 if perfect else -5

    attempt, created = PuzzleRatingAttempt.objects.get_or_create(
        user=request.user,
        category_slug=puzzle['slug'],
        puzzle_index=puzzle_index,
        defaults={
            'perfect': perfect,
            'delta': delta,
        },
    )

    if not created:
        return JsonResponse(
            {
                'ok': True,
                'perfect': attempt.perfect,
                'delta': 0,
                'puzzle_rating': profile.puzzle_rating,
                'already_recorded': True,
                'message': 'Rated result already recorded for this puzzle.',
            }
        )

    profile.puzzle_rating = max(0, profile.puzzle_rating + delta)
    profile.save(update_fields=['puzzle_rating'])

    return JsonResponse(
        {
            'ok': True,
            'perfect': perfect,
            'delta': delta,
            'puzzle_rating': profile.puzzle_rating,
        }
    )


@login_required

# Tutor selection page lists every coach because tutors are not locked.
def tutors_view(request):
    return render(
        request,
        'main/tutors.html',
        {
            'tutors': get_tutors(),
        },
    )


@login_required
def tutor_game_view(request, tutor_slug):
    tutor = find_tutor(tutor_slug)

    if tutor is None:
        return redirect('tutors')

    progress = get_progress(request)
    game = get_or_create_tutor_game(request, tutor)
    game_state = serialize_tutor_game(game, tutor, progress['player_elo'])

    return render(
        request,
        'main/tutor_game.html',
        {
            'tutor': tutor,
            'game_state': game_state,
            'tutorial_lessons': tutorial_lessons() if tutor.get('tutorial') else [],
        },
    )


@login_required
@require_POST
def tutor_start_view(request, tutor_slug):
    tutor = find_tutor(tutor_slug)

    if tutor is None:
        return JsonResponse({'ok': False, 'error': 'This tutor is not available.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid start request.'}, status=400)

    player_color = payload.get('color')
    if player_color not in ('white', 'black'):
        return JsonResponse({'ok': False, 'error': 'Choose white or black pieces.'}, status=400)

    if tutor.get('tutorial'):
        game = create_tutor_game(tutor)
    else:
        game = start_tutor_game(tutor, player_color)

    request.session[TUTOR_SESSION_KEY] = game
    request.session.modified = True
    progress = get_progress(request)

    return JsonResponse({'ok': True, 'state': serialize_tutor_game(game, tutor, progress['player_elo'])})


@login_required
@require_POST

# AJAX endpoint applies the user's tutor move and returns updated feedback.
def tutor_move_view(request, tutor_slug):
    tutor = find_tutor(tutor_slug)

    if tutor is None:
        return JsonResponse({'ok': False, 'error': 'This tutor is not available.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid move request.'}, status=400)

    move_uci = payload.get('move')

    if not move_uci:
        return JsonResponse({'ok': False, 'error': 'No move was provided.'}, status=400)

    game = request.session.get(TUTOR_SESSION_KEY)

    if not game or game.get('bot_slug') != tutor_slug or game.get('mode') != 'tutor':
        game = create_tutor_game(tutor)

    game, error = apply_tutor_player_move(game, tutor, move_uci)
    request.session[TUTOR_SESSION_KEY] = game
    request.session.modified = True
    progress = get_progress(request)
    state = serialize_tutor_game(game, tutor, progress['player_elo'])

    if error:
        return JsonResponse({'ok': False, 'error': error, 'state': state}, status=400)

    return JsonResponse({'ok': True, 'state': state})


@login_required
@require_POST
def tutor_reply_view(request, tutor_slug):
    tutor = find_tutor(tutor_slug)

    if tutor is None:
        return JsonResponse({'ok': False, 'error': 'This tutor is not available.'}, status=404)

    game = request.session.get(TUTOR_SESSION_KEY)

    if not game or game.get('bot_slug') != tutor_slug or game.get('mode') != 'tutor':
        return JsonResponse({'ok': False, 'error': 'Start a tutor game first.'}, status=400)

    game, error = apply_tutor_reply_move(game, tutor)
    request.session[TUTOR_SESSION_KEY] = game
    request.session.modified = True
    progress = get_progress(request)
    state = serialize_tutor_game(game, tutor, progress['player_elo'])

    if error:
        return JsonResponse({'ok': False, 'error': error, 'state': state}, status=400)

    return JsonResponse({'ok': True, 'state': state})


@login_required
@require_POST

# Undo endpoint supports the tutoring goal of learning from mistakes.
def tutor_undo_view(request, tutor_slug):
    tutor = find_tutor(tutor_slug)

    if tutor is None:
        return JsonResponse({'ok': False, 'error': 'This tutor is not available.'}, status=404)

    game = request.session.get(TUTOR_SESSION_KEY)

    if not game or game.get('bot_slug') != tutor_slug or game.get('mode') != 'tutor':
        game = create_tutor_game(tutor)

    game = undo_tutor_move(game, tutor)
    request.session[TUTOR_SESSION_KEY] = game
    request.session.modified = True
    progress = get_progress(request)
    return JsonResponse({'ok': True, 'state': serialize_tutor_game(game, tutor, progress['player_elo'])})


@login_required
def bot_theme_view(request, bot_slug):
    progress = get_progress(request)
    bots = get_bot_ladder(progress)
    selected_bot = find_bot(bot_slug, progress)

    if selected_bot is None or not selected_bot['unlocked']:
        return redirect('play')

    return render(
        request,
        'main/bot_themes.html',
        {
            'bot': selected_bot,
            'themes': get_bot_themes(),
            'player_elo': progress['player_elo'],
            'bots_defeated': len(progress.get('defeated_bots', [])),
            'total_bots': len(bots),
        },
    )


@login_required
def bot_game_view(request, bot_slug, theme_slug):
    progress = get_progress(request)
    selected_bot = find_bot(bot_slug, progress)
    selected_theme = find_theme(theme_slug)

    if selected_bot is None or selected_theme is None or not selected_bot['unlocked']:
        return redirect('play')

    game = get_or_create_game(request, selected_bot, selected_theme)
    game_state = serialize_game(game, selected_bot, selected_theme, progress['player_elo'])

    return render(
        request,
        'main/bot_game.html',
        {
            'bot': selected_bot,
            'theme': selected_theme,
            'player_elo': game_state['player_elo'],
            'game_state': game_state,
            'position_number': game_state['position_number'],
            'position_label': 'Starting Position',
        },
    )


@login_required
@require_POST

# Bot move endpoint validates player moves before saving the new board state.
def bot_move_view(request, bot_slug, theme_slug):
    progress = get_progress(request)
    selected_bot = find_bot(bot_slug, progress)
    selected_theme = find_theme(theme_slug)

    if selected_bot is None or selected_theme is None or not selected_bot['unlocked']:
        return JsonResponse({'ok': False, 'error': 'This bot is not available.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid move request.'}, status=400)

    move_uci = payload.get('move')
    if not move_uci:
        return JsonResponse({'ok': False, 'error': 'No move was provided.'}, status=400)

    game = request.session.get(GAME_SESSION_KEY)
    if not game or game.get('bot_slug') != bot_slug or game.get('theme_slug') != theme_slug:
        game = create_game(selected_bot['slug'], selected_theme['slug'], selected_theme['intro'], selected_theme['name'])

    game, error = apply_player_move(game, selected_bot, selected_theme, move_uci)

    if not error and game.get('winner') == 'player':
        progress = record_bot_win(request, selected_bot)

    request.session[GAME_SESSION_KEY] = game
    request.session.modified = True

    state = serialize_game(game, selected_bot, selected_theme, progress['player_elo'])

    if error:
        return JsonResponse({'ok': False, 'error': error, 'state': state}, status=400)

    return JsonResponse({'ok': True, 'state': state})


@login_required
@require_POST
def bot_start_view(request, bot_slug, theme_slug):
    progress = get_progress(request)
    selected_bot = find_bot(bot_slug, progress)
    selected_theme = find_theme(theme_slug)

    if selected_bot is None or selected_theme is None or not selected_bot['unlocked']:
        return JsonResponse({'ok': False, 'error': 'This bot is not available.'}, status=404)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid start request.'}, status=400)

    player_color = payload.get('color')
    if player_color not in ('white', 'black'):
        return JsonResponse({'ok': False, 'error': 'Choose white or black pieces.'}, status=400)

    game = start_game(selected_bot, selected_theme, player_color)
    request.session[GAME_SESSION_KEY] = game
    request.session.modified = True

    state = serialize_game(game, selected_bot, selected_theme, progress['player_elo'])
    return JsonResponse({'ok': True, 'state': state})


@login_required
@require_POST
def bot_reply_view(request, bot_slug, theme_slug):
    progress = get_progress(request)
    selected_bot = find_bot(bot_slug, progress)
    selected_theme = find_theme(theme_slug)

    if selected_bot is None or selected_theme is None or not selected_bot['unlocked']:
        return JsonResponse({'ok': False, 'error': 'This bot is not available.'}, status=404)

    game = request.session.get(GAME_SESSION_KEY)
    if not game or game.get('bot_slug') != bot_slug or game.get('theme_slug') != theme_slug:
        return JsonResponse({'ok': False, 'error': 'Start a match before asking the bot to move.'}, status=400)

    game, error = apply_bot_move(game, selected_bot, selected_theme)

    if not error and game.get('winner') == 'player':
        progress = record_bot_win(request, selected_bot)

    request.session[GAME_SESSION_KEY] = game
    request.session.modified = True

    state = serialize_game(game, selected_bot, selected_theme, progress['player_elo'])

    if error:
        return JsonResponse({'ok': False, 'error': error, 'state': state}, status=400)

    return JsonResponse({'ok': True, 'state': state})



# Sends the account verification email used during registration.
def send_verification_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    verification_path = reverse('verify_email', kwargs={'uidb64': uid, 'token': token})
    verification_url = request.build_absolute_uri(verification_path)
    subject = 'Verify your Chess Tutor account'
    message = render_to_string(
        'main/emails/verification_email.txt',
        {
            'user': user,
            'verification_url': verification_url,
        },
    )

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )



# Registration creates an inactive account until the email address is verified.
def register_view(request):
    if request.user.is_authenticated:
        return redirect('play')

    if request.method == 'POST':
        form = ChessTutorUserCreationForm(request.POST)

        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()

            try:
                send_verification_email(request, user)
            except SMTPException:
                user.delete()
                form.add_error(
                    None,
                    'The verification email could not be sent. Check the Gmail address and app password, then try again.',
                )
            else:
                return redirect('verification_sent')
    else:
        form = ChessTutorUserCreationForm()

    return render(request, 'main/register.html', {'form': form})


def verification_sent_view(request):
    return render(request, 'main/verification_sent.html')


def verify_email_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        login(request, user)
        messages.success(request, 'Email verified. Welcome to Chess Tutor.')
        return redirect('play')

    return render(request, 'main/verification_invalid.html')



# Login redirects authenticated users straight into the play experience.
def login_view(request):
    if request.user.is_authenticated:
        return redirect('play')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            login(request, form.get_user())
            messages.success(request, 'Welcome back.')
            return redirect('play')
    else:
        form = AuthenticationForm()

    return render(request, 'main/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')
