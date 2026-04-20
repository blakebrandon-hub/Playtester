"""
playtester_agent.py — Generic AI Playtester Agent Template
===========================================================

A second AI agent that plays your AI-narrated game, logs observations,
detects anomalies, and writes a structured QA report.

Requires your game's narrator backend running separately (see GAME_API_URL).
Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY in env.

ENDPOINTS
---------
POST /api/playtester/start    — Begin a new session
POST /api/playtester/step     — Agent takes one action
GET  /api/playtester/state    — Current game state + session stats
POST /api/playtester/report   — Agent writes a QA report
POST /api/playtester/reset    — Reset session

Run standalone:
    python playtester_agent.py
Then in another terminal:
    python playtester_cli.py
"""

import re
import json
import copy
import time
import logging
import os
import requests
from datetime import datetime
from flask import Flask, Blueprint, request, jsonify
from flask_cors import CORS

logger = logging.getLogger(__name__)

# ── Optional AI clients ────────────────────────────────────────────────────────
try:
    from anthropic import Anthropic
    _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]) if os.environ.get("ANTHROPIC_API_KEY") else None
except Exception:
    _anthropic = None

try:
    from google import genai
    from google.genai import types as gtypes
    _gemini = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
except Exception:
    _gemini = None

try:
    from openai import OpenAI
    _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"]) if os.environ.get("OPENAI_API_KEY") else None
except Exception:
    _openai = None


# ── Game backend ───────────────────────────────────────────────────────────────
GAME_API_URL    = "http://localhost:5000/api/chat"     # Your narrator endpoint
ARCHIVE_API_URL = "http://localhost:5000/api/archive"  # Your summarisation endpoint (optional)


# ─────────────────────────────────────────────────────────────────────────────
# TODO: DEFAULT GAME STATE
# Replace these fields with the variables your game actually tracks.
# This object is updated each turn by parse_tags() / apply_tags().
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_GAME_STATE = {
    # TODO: add your game's state variables here, e.g.:
    # "hp": 100,
    # "location": "Starting Area",
    # "inventory": [],
}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE  (do not edit this structure — edit DEFAULT_GAME_STATE above)
# ─────────────────────────────────────────────────────────────────────────────

session = {
    "active":           False,
    "game":             "My Game",   # TODO: rename
    "turn":             0,
    "started_at":       None,
    "game_state":       copy.deepcopy(DEFAULT_GAME_STATE),
    "conversation_log": [],
    "summaries":        [],
    "playtest_notes":   [],
    "paths_explored":   [],
    "bugs_found":       [],
}


# ─────────────────────────────────────────────────────────────────────────────
# TODO: TAG PARSER
# Your narrator should emit structured tags so the agent can track state
# without relying on prose. Parse them here.
#
# Example tags your narrator might emit:
#   [HP: +10]
#   [LOC: Forest | Biome]
#   [ITEM: Sword | Quantity: +1]
#
# Return a dict of changes — whatever shape suits your game.
# ─────────────────────────────────────────────────────────────────────────────

def parse_tags(text: str) -> dict:
    changes = {}

    # TODO: add your tag parsing here, e.g.:
    # m = re.search(r'\[HP:\s*([+-]?\d+)\]', text, re.IGNORECASE)
    # if m:
    #     changes["hp_delta"] = int(m.group(1))

    return changes


def apply_tags(gs: dict, changes: dict) -> dict:
    """Apply parsed tag changes to the game state dict."""
    # TODO: apply your changes to gs, e.g.:
    # gs["hp"] = max(0, min(100, gs["hp"] + changes.get("hp_delta", 0)))

    return gs


def strip_tags(text: str) -> str:
    """Remove all state tags from narrator prose before displaying."""
    # TODO: add patterns for each tag type your narrator emits, e.g.:
    # patterns = [r'\[HP:\s*[+-]?\d+\]', r'\[LOC:[^\]]+\]']
    patterns = []
    result = text
    for p in patterns:
        result = re.sub(p, '', result, flags=re.IGNORECASE)
    return result.strip()


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT BUILDER
# Assembles the current game state into a text block sent to the agent
# each turn, so it knows what's happening in the world.
# ─────────────────────────────────────────────────────────────────────────────

def build_context(gs: dict, conversation_log: list, summaries: list) -> str:
    archive_block = ""
    for i, s in enumerate(summaries):
        archive_block += f"[Archive {i+1}]: {s}\n"

    recent = conversation_log[-10:]
    history = "\n".join(
        f"{'PLAYER' if e['role'] == 'Player' else 'NARRATOR'}: {e['message']}"
        for e in recent
    )

    # TODO: add your game's state fields to this context block, e.g.:
    # return f"""=== GAME STATE ===
    # HP: {gs['hp']}
    # Location: {gs['location']}
    # ...
    # {archive_block}=== RECENT LOG ===
    # {history}
    # """

    return f"""=== GAME STATE ===
{json.dumps(gs, indent=2)}

{archive_block}=== RECENT LOG ===
{history}
"""


# ─────────────────────────────────────────────────────────────────────────────
# TODO: NARRATOR SYSTEM PROMPT
# This is sent to your game's narrator each turn alongside the player action.
# Define your world, rules, tone, and the structured tags the narrator must emit.
# ─────────────────────────────────────────────────────────────────────────────

NARRATOR_SYSTEM_PROMPT = """
# TODO: Replace this with your game's narrator system prompt.

You are the narrator of [YOUR GAME].

## WORLD
[Describe your world here.]

## RULES
[Describe your game mechanics here.]

## STATE TAGS
[Define the structured tags your narrator must emit, e.g.:]

[HP: +/-N]       — health change
[LOC: Name]      — location change
[ITEM: Name | Quantity: +/-N]  — inventory change

Prose FIRST. Tags LAST, after a blank line.
"""


# ─────────────────────────────────────────────────────────────────────────────
# TODO: PLAYTESTER SYSTEM PROMPT
# This defines the agent's persona and testing approach.
# Give it a name, a role, and tell it what to test.
# ─────────────────────────────────────────────────────────────────────────────

PLAYTESTER_SYSTEM_PROMPT = """
You are [NAME] — a warm, experienced QA playtester who loves narrative games.

You are playtesting "[YOUR GAME]".

YOUR JOB:
- Explore the game world methodically.
- Test mechanics deliberately — try edge cases, use items in unusual ways,
  push toward locked or restricted content.
- Note anything that feels unclear, broken, or surprising.

FORMAT YOUR RESPONSES EXACTLY LIKE THIS:
  say: <the exact command to send to the game>
  reflect: <optional playtester observation>

Examples:
  say: Go north toward the abandoned tower.
  say: Use the key on the locked chest.
  reflect: The narrator handled that edge case gracefully.

Trust the game state provided. Don't invent items or resources you don't have.
"""


# ─────────────────────────────────────────────────────────────────────────────
# AI CALL  (agent choosing its next action)
# ─────────────────────────────────────────────────────────────────────────────

def call_agent(prompt: str) -> str:
    if _anthropic:
        resp = _anthropic.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=PLAYTESTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    elif _openai:
        resp = _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": PLAYTESTER_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_completion_tokens=1024,
        )
        return resp.choices[0].message.content

    elif _gemini:
        resp = _gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                system_instruction=PLAYTESTER_SYSTEM_PROMPT,
                max_output_tokens=1024,
                temperature=0.9,
            ),
        )
        return resp.text

    else:
        raise RuntimeError("No AI client available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY.")


# ─────────────────────────────────────────────────────────────────────────────
# AGENT ACTION SELECTOR
# ─────────────────────────────────────────────────────────────────────────────

def agent_choose_action(gs: dict, conversation_log: list,
                        summaries: list, playtest_notes: list) -> dict:
    notes_str = "\n".join(f"- {n}" for n in playtest_notes[-5:]) or "None yet."
    game_context = build_context(gs, conversation_log, summaries)

    user_message = f"""
[PLAYTESTING — Turn {session['turn'] + 1}]

GAME STATE:
{game_context}

YOUR RECENT PLAYTEST NOTES:
{notes_str}

Decide the player's next action.
Use `say:` for the exact command, and optionally `reflect:` for your observation.
"""

    try:
        raw = call_agent(user_message)

        say_match     = re.search(r'^say:\s*(.+)$',     raw, re.MULTILINE | re.IGNORECASE)
        reflect_match = re.search(r'^reflect:\s*(.+)$', raw, re.MULTILINE | re.IGNORECASE)

        action     = say_match.group(1).strip()     if say_match     else raw.strip()
        reflection = reflect_match.group(1).strip() if reflect_match else None

        if not action:
            action = "I look around carefully."

        return {"action": action, "reflection": reflection, "raw": raw}

    except Exception as e:
        logger.error(f"Agent action selection failed: {e}")
        return {"action": "I look around carefully.", "reflection": None, "raw": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# GAME API CALL
# ─────────────────────────────────────────────────────────────────────────────

RETRY_ATTEMPTS   = 4
RETRY_BASE_DELAY = 5  # seconds; doubles each attempt (5, 10, 20, 40)


def send_to_game(player_action: str, gs: dict,
                 conversation_log: list, summaries: list) -> dict:
    context = build_context(gs, conversation_log, summaries)
    payload = {
        "system_prompt": NARRATOR_SYSTEM_PROMPT,
        "context":       context,
        "player_action": player_action,
    }

    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(GAME_API_URL, json=payload, timeout=90)
            resp.raise_for_status()
            return {"text": resp.json().get("text", ""), "error": None}
        except Exception as e:
            last_error   = e
            err_str      = str(e)
            is_transient = any(code in err_str for code in ("500", "503", "429", "UNAVAILABLE"))

            if is_transient and attempt < RETRY_ATTEMPTS:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Game API error (attempt {attempt}/{RETRY_ATTEMPTS}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                break

    logger.error(f"Game API call failed after {RETRY_ATTEMPTS} attempts: {last_error}")
    return {"text": "", "error": str(last_error)}


def maybe_archive(conversation_log: list, summaries: list) -> list:
    """Every 12 turns, compress the log via /api/archive to save context space."""
    if len(conversation_log) < 12 or len(conversation_log) % 12 != 0:
        return summaries

    segment  = conversation_log[-12:]
    log_text = "\n".join(
        f"{'PLAYER' if e['role'] == 'Player' else 'NARRATOR'}: {e['message']}"
        for e in segment
    )

    # TODO: customise this prompt to match your game's content
    archivist_prompt = (
        "You are a concise archivist for a narrative game. "
        "Write a dense 2-4 sentence summary of this log segment: where the player went, "
        "what happened, items used or gained, and any notable state changes. "
        "Third person, past tense, factual. No padding."
    )

    try:
        resp = requests.post(ARCHIVE_API_URL, json={
            "context":            log_text,
            "system_instruction": archivist_prompt,
        }, timeout=30)
        resp.raise_for_status()
        summary = resp.json().get("text", "")
        if summary:
            return ([summary] + summaries)[:4]
    except Exception as e:
        logger.warning(f"Archive call failed: {e}")

    return summaries


# ─────────────────────────────────────────────────────────────────────────────
# TODO: BUG DETECTION
# Add checks here that are specific to your game's tag schema.
# These run automatically each turn and flag anomalies in session["bugs_found"].
# ─────────────────────────────────────────────────────────────────────────────

def detect_anomalies(action: str, response: str, changes: dict, gs: dict) -> list:
    bugs = []

    # TODO: add game-specific checks, e.g.:
    #
    # action_lower = action.lower()
    # response_lower = response.lower()
    #
    # # Movement without location tag
    # if any(kw in action_lower for kw in ["go to", "travel to", "enter"]) and not changes.get("location"):
    #     bugs.append(f"[Missing LOC Tag] Movement action emitted no location tag.")
    #
    # # HP at zero
    # if gs.get("hp", 100) <= 0:
    #     bugs.append("[Player Down] HP reached 0.")

    return bugs


# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_report() -> str:
    notes_text = "\n".join(session["playtest_notes"]) or "No notes recorded."
    bugs_text  = "\n".join(session["bugs_found"])     or "No bugs detected."
    paths_text = "\n".join(session["paths_explored"]) or "None."

    # TODO: add your game's state fields to this summary
    prompt = f"""
You have just finished a playtesting session.

SESSION SUMMARY:
- Turns Played: {session['turn']}
- Started:      {session['started_at']}
- Game State:   {json.dumps(session['game_state'], indent=2)}

LOCATIONS VISITED:
{paths_text}

YOUR PLAYTEST NOTES:
{notes_text}

AUTOMATED BUG DETECTIONS:
{bugs_text}

Write a structured playtester's report:

1. **Overall Impression** — How did the game feel?
2. **Narrative Quality** — Was the narrator's prose consistent and engaging?
3. **Mechanics** — Did the core mechanics work as expected?
4. **Tag System** — Did structured state tags behave correctly?
5. **Bugs & Inconsistencies** — List everything that felt wrong or broken.
6. **Recommendations** — What would make this game better?

Be honest. Be specific. Reference actual moments from the session where possible.
"""
    try:
        return call_agent(prompt)
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        return f"Report generation failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# CORE STEP LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def playtester_step() -> dict:
    if not session["active"]:
        return {"error": "No active session. Call /api/playtester/start first."}

    agent_result = agent_choose_action(
        session["game_state"],
        session["conversation_log"],
        session["summaries"],
        session["playtest_notes"],
    )

    action     = agent_result["action"]
    reflection = agent_result["reflection"]

    game_result = send_to_game(
        action,
        session["game_state"],
        session["conversation_log"],
        session["summaries"],
    )

    if game_result["error"]:
        return {"error": game_result["error"], "action": action}

    raw_response   = game_result["text"]
    clean_response = strip_tags(raw_response)

    changes = parse_tags(raw_response)
    session["game_state"] = apply_tags(session["game_state"], changes)

    ts = datetime.now().isoformat()
    session["conversation_log"].append({"role": "Player",  "message": action,       "timestamp": ts})
    session["conversation_log"].append({"role": "Narrator","message": raw_response, "timestamp": ts})

    session["summaries"] = maybe_archive(session["conversation_log"], session["summaries"])

    if reflection:
        session["playtest_notes"].append(f"[Turn {session['turn'] + 1}] {reflection}")

    # Track unique locations visited
    loc = str(session["game_state"].get("location", "unknown"))
    if loc not in session["paths_explored"]:
        session["paths_explored"].append(loc)

    bugs = detect_anomalies(action, raw_response, changes, session["game_state"])
    session["bugs_found"].extend(bugs)

    session["turn"] += 1

    return {
        "turn":           session["turn"],
        "action":         action,
        "response":       clean_response,
        "reflection":     reflection,
        "bugs_detected":  bugs,
        "state_snapshot": session["game_state"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# FLASK APP + ROUTES
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)
bp  = Blueprint('playtester', __name__)


@bp.route('/api/playtester/start', methods=['POST'])
def start_session():
    session.update({
        "active":           True,
        "turn":             0,
        "started_at":       datetime.now().isoformat(),
        "game_state":       copy.deepcopy(DEFAULT_GAME_STATE),
        "conversation_log": [],
        "summaries":        [],
        "playtest_notes":   [],
        "paths_explored":   [],
        "bugs_found":       [],
    })
    return jsonify({"ok": True, "game_state": session["game_state"]})


@bp.route('/api/playtester/step', methods=['POST'])
def step():
    return jsonify(playtester_step())


@bp.route('/api/playtester/run', methods=['POST'])
def run_auto():
    data    = request.json or {}
    n_turns = min(int(data.get("turns", 5)), 50)
    delay   = float(data.get("delay", 1.5))
    if not session["active"]:
        return jsonify({"error": "No active session."}), 400
    results = []
    for _ in range(n_turns):
        result = playtester_step()
        results.append(result)
        if result.get("error"):
            break
        time.sleep(delay)
    return jsonify({"turns_played": len(results), "results": results, "total_turns": session["turn"]})


@bp.route('/api/playtester/state', methods=['GET'])
def get_state():
    return jsonify({
        "session_active": session["active"],
        "turn":           session["turn"],
        "started_at":     session["started_at"],
        "game_state":     session["game_state"],
        "paths_explored": session["paths_explored"],
        "playtest_notes": session["playtest_notes"],
        "bugs_found":     session["bugs_found"],
        "recent_log":     session["conversation_log"][-6:],
    })


@bp.route('/api/playtester/report', methods=['POST'])
def report():
    if session["turn"] == 0:
        return jsonify({"error": "No turns played yet."}), 400
    report_text = generate_report()
    return jsonify({
        "report":         report_text,
        "turns_covered":  session["turn"],
        "bugs_found":     len(session["bugs_found"]),
        "notes_recorded": len(session["playtest_notes"]),
    })


@bp.route('/api/playtester/reset', methods=['POST'])
def reset():
    session.update({
        "active": False, "turn": 0, "started_at": None,
        "game_state":       copy.deepcopy(DEFAULT_GAME_STATE),
        "conversation_log": [], "summaries": [],
        "playtest_notes":   [], "paths_explored": [], "bugs_found": [],
    })
    return jsonify({"ok": True, "message": "Session reset."})


app.register_blueprint(bp)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN  (standalone server on :7001)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ai_status = []
    if _anthropic: ai_status.append("Claude ✓")
    if _openai:    ai_status.append("OpenAI ✓")
    if _gemini:    ai_status.append("Gemini ✓")
    if not ai_status:
        print("⚠️  No AI keys found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY.")

    print("\n" + "=" * 60)
    print("🎮 AI PLAYTESTER AGENT")
    print("=" * 60)
    print(f"  AI Backend:   {' | '.join(ai_status) or 'None configured'}")
    print(f"  Game Backend: {GAME_API_URL}")
    print(f"  Listening on: http://localhost:7001")
    print("=" * 60 + "\n")

    app.run(debug=False, port=7001)
