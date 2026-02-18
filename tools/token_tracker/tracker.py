#!/usr/bin/env python3
"""
token_tracker — Log and summarize Claude Code session usage.

Usage:
    python tools/token_tracker/tracker.py log       # Record a new session
    python tools/token_tracker/tracker.py summary   # Show all past sessions
"""

import json
import sys
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent / "usage_log.json"


def load_log():
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE) as f:
        return json.load(f)


def save_log(entries):
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def log_session():
    print("=== Log Claude Code Session ===")
    print("(Run /cost in Claude Code to get token counts before closing the session.)\n")

    date_str = datetime.now().strftime("%Y-%m-%d")
    date_input = input(f"Date [{date_str}]: ").strip()
    date = date_input if date_input else date_str

    description = input("What did you work on? ").strip()

    input_tokens_str = input("Input tokens (from /cost, or press Enter to skip): ").strip()
    output_tokens_str = input("Output tokens (from /cost, or press Enter to skip): ").strip()
    cost_str = input("Estimated cost (e.g. $0.12, or press Enter to skip): ").strip()
    notes = input("Any notes? (optional): ").strip()

    entry = {
        "date": date,
        "description": description,
        "input_tokens": int(input_tokens_str) if input_tokens_str.isdigit() else None,
        "output_tokens": int(output_tokens_str) if output_tokens_str.isdigit() else None,
        "cost": cost_str if cost_str else None,
        "notes": notes if notes else None,
    }

    entries = load_log()
    entries.append(entry)
    save_log(entries)

    print(f"\nSaved. Total sessions logged: {len(entries)}")


def show_summary():
    entries = load_log()

    if not entries:
        print("No sessions logged yet. Run: python tools/token_tracker/tracker.py log")
        return

    print(f"=== Claude Code Session Summary ({len(entries)} sessions) ===\n")

    total_input = 0
    total_output = 0
    for i, e in enumerate(entries, 1):
        cost_display = e.get("cost") or "—"
        input_t = e.get("input_tokens")
        output_t = e.get("output_tokens")
        tokens_display = ""
        if input_t is not None or output_t is not None:
            tokens_display = f"  [{input_t or '?'} in / {output_t or '?'} out tokens]"
            if input_t:
                total_input += input_t
            if output_t:
                total_output += output_t

        notes_display = f"  Note: {e['notes']}" if e.get("notes") else ""
        print(f"{i:3}. [{e['date']}] {e['description']}")
        print(f"     Cost: {cost_display}{tokens_display}{notes_display}")
        print()

    print("--- Totals ---")
    if total_input or total_output:
        print(f"Tokens: {total_input:,} input / {total_output:,} output")
    print(f"Sessions: {len(entries)}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("log", "summary"):
        print("Usage:")
        print("  python tools/token_tracker/tracker.py log       # Record a new session")
        print("  python tools/token_tracker/tracker.py summary   # Show all past sessions")
        sys.exit(1)

    if sys.argv[1] == "log":
        log_session()
    elif sys.argv[1] == "summary":
        show_summary()


if __name__ == "__main__":
    main()
