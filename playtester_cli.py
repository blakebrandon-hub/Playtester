"""
playtester_cli.py — Terminal runner for the AI Playtester Agent
===============================================================

Watches the playtester agent play through your game in your terminal.
Requires both your game server (app.py on :5000) and the playtester
agent (playtester_agent.py on :7001) to be running.

Usage:
    python playtester_cli.py                  # 20 turns, auto
    python playtester_cli.py --turns 10       # 10 turns
    python playtester_cli.py --delay 3        # 3s between turns
    python playtester_cli.py --step           # manual step mode
    python playtester_cli.py --report-only    # just generate a report
"""

import argparse
import requests
import time
import sys

PLAYTESTER_BASE = "http://localhost:7001"


# ── Terminal colors ────────────────────────────────────────────────────────────
class C:
    GOLD   = "\033[93m"
    BLUE   = "\033[94m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    GREY   = "\033[90m"
    WHITE  = "\033[97m"
    CYAN   = "\033[96m"
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"


def divider(char="─", width=72, color=C.GREY):
    print(f"{color}{char * width}{C.RESET}")


def header(text, color=C.GREEN):
    divider("═", color=color)
    print(f"{color}{C.BOLD}  {text}{C.RESET}")
    divider("═", color=color)


def word_wrap(text, indent="    ", width=76):
    words = text.split()
    line  = indent
    for word in words:
        if len(line) + len(word) + 1 > width:
            print(line)
            line = indent + word + " "
        else:
            line += word + " "
    if line.strip():
        print(line)


def print_turn(result: dict):
    turn       = result.get("turn", "?")
    action     = result.get("action", "")
    response   = result.get("response", "")
    reflection = result.get("reflection")
    bugs       = result.get("bugs_detected", [])

    print(f"\n{C.GREEN}{C.BOLD}  Turn {turn}{C.RESET}")
    divider("·", 52)

    print(f"{C.GREEN}  PLAYER:{C.RESET} {action}")
    print(f"\n{C.BLUE}  NARRATOR:{C.RESET}")
    word_wrap(response)

    # TODO: print relevant fields from result["state_snapshot"] here, e.g.:
    # snap = result.get("state_snapshot", {})
    # print(f"\n  Location: {snap.get('location', '?')}")
    # print(f"  HP: {snap.get('hp', '?')}")

    if reflection:
        print(f"\n{C.CYAN}  💭 Tester:{C.RESET} {C.DIM}{reflection}{C.RESET}")

    for bug in bugs:
        print(f"\n{C.RED}  ⚠  BUG:{C.RESET} {bug}")


def print_report(report_data: dict):
    header("PLAYTESTER REPORT", C.GREEN)
    print(f"{C.GREY}  Turns Covered:  {report_data.get('turns_covered', '?')}")
    print(f"  Bugs Detected:  {report_data.get('bugs_found', '?')}")
    print(f"  Notes Recorded: {report_data.get('notes_recorded', '?')}{C.RESET}\n")
    divider()
    print(f"{C.WHITE}{report_data.get('report', 'No report generated.')}{C.RESET}")
    divider("═")


def api(method, path, **kwargs):
    url = PLAYTESTER_BASE + path
    try:
        if method == "GET":
            r = requests.get(url, timeout=120)
        else:
            r = requests.post(url, timeout=120, **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"\n{C.RED}✗  Cannot connect to {PLAYTESTER_BASE}.")
        print(f"   Is playtester_agent.py running?{C.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{C.RED}✗  API error: {e}{C.RESET}")
        return {"error": str(e)}


# ── Step with retry ───────────────────────────────────────────────────────────

RETRY_ATTEMPTS   = 4
RETRY_BASE_DELAY = 5

def step_with_retry():
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        result = api("POST", "/api/playtester/step")
        error  = result.get("error", "")

        if not error:
            return result

        is_transient = "503" in str(error) or "UNAVAILABLE" in str(error) or "temporarily" in str(error).lower()

        if is_transient and attempt < RETRY_ATTEMPTS:
            wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(f"{C.GOLD}  ⏳ 503 — retrying in {wait}s (attempt {attempt}/{RETRY_ATTEMPTS})...{C.RESET}")
            time.sleep(wait)
        else:
            return result

    return result


# ── Run modes ─────────────────────────────────────────────────────────────────

def run_auto(turns: int, delay: float):
    header(f"AI PLAYTESTER — {turns} TURNS")
    print(f"{C.GREEN}▸ Starting session...{C.RESET}")
    start = api("POST", "/api/playtester/start")
    if start.get("error"):
        print(f"{C.RED}Error: {start['error']}{C.RESET}")
        return
    print(f"{C.GREEN}✓ Playing {turns} turns with {delay}s delay.{C.RESET}\n")

    for i in range(turns):
        result = step_with_retry()
        if result.get("error"):
            print(f"{C.RED}Error on turn {i+1}: {result['error']}{C.RESET}")
            break
        print_turn(result)
        if i < turns - 1:
            time.sleep(delay)


def run_step_mode():
    header("AI PLAYTESTER — MANUAL STEP MODE")
    print(f"{C.GREEN}▸ Starting session...{C.RESET}")
    api("POST", "/api/playtester/start")
    print(f"{C.GREEN}✓ Session started.{C.RESET}")
    print(f"{C.GREY}  ENTER = next turn  │  r = report  │  s = state  │  q = quit{C.RESET}\n")

    while True:
        try:
            cmd = input(f"\n{C.GREEN}[ENTER / r / s / q]:{C.RESET} ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C.GREY}Interrupted.{C.RESET}")
            break

        if cmd == 'q':
            break

        elif cmd == 'r':
            report_data = api("POST", "/api/playtester/report")
            if report_data.get("error"):
                print(f"{C.RED}{report_data['error']}{C.RESET}")
            else:
                print_report(report_data)

        elif cmd == 's':
            state = api("GET", "/api/playtester/state")
            if state.get("error"):
                print(f"{C.RED}{state['error']}{C.RESET}")
            else:
                print(f"\n{C.CYAN}Turn: {state.get('turn', 0)}{C.RESET}")
                print(f"{C.CYAN}Notes: {len(state.get('playtest_notes', []))}{C.RESET}")
                print(f"{C.CYAN}Bugs:  {len(state.get('bugs_found', []))}{C.RESET}")
                # TODO: print relevant game state fields here

        else:
            result = step_with_retry()
            if result.get("error"):
                print(f"{C.RED}{result['error']}{C.RESET}")
            else:
                print_turn(result)


def main():
    parser = argparse.ArgumentParser(
        description="AI Playtester CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python playtester_cli.py                  # 20 turns, auto
  python playtester_cli.py --turns 10       # 10 turns
  python playtester_cli.py --delay 3        # 3s between turns
  python playtester_cli.py --step           # manual step mode
  python playtester_cli.py --report-only    # generate report only
        """
    )
    parser.add_argument("--turns",       type=int,   default=20,  help="Number of turns to auto-play (default: 20)")
    parser.add_argument("--delay",       type=float, default=2.0, help="Seconds between turns (default: 2.0)")
    parser.add_argument("--step",        action="store_true",     help="Manual step mode")
    parser.add_argument("--report-only", action="store_true",     help="Generate a report from the current session")
    args = parser.parse_args()

    if args.report_only:
        report_data = api("POST", "/api/playtester/report")
        if report_data.get("error"):
            print(f"{C.RED}{report_data['error']}{C.RESET}")
        else:
            print_report(report_data)

    elif args.step:
        run_step_mode()

    else:
        run_auto(args.turns, args.delay)


if __name__ == "__main__":
    main()
