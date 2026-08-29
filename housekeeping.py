"""Out-of-band mem0 housekeeping.

Deletes memories by id (prompt + synchronous). Run this well before any
pipeline run — never inline with one — so slow-settling deletes can't race
fresh writes.

Usage:
    python housekeeping.py --list           # show what's in the store
    python housekeeping.py                   # delete ALL memories (asks to confirm)
    python housekeeping.py --keep-latest     # delete all except the most recent run
    python housekeeping.py --dry-run         # show what would be deleted, delete nothing
    python housekeeping.py --yes             # skip the confirmation prompt
"""

import argparse

from tools.memory import (
    delete_memories,
    list_all_memories,
    read_latest_run,
    _run_ts_of,
)


def _describe(memory):
    md = memory.get("metadata") or {}
    who = md.get("competitor") or md.get("company") or "?"
    return f"[{md.get('category')}/{who}] run_ts={md.get('run_ts')}"


def main():
    parser = argparse.ArgumentParser(description="Delete mem0 memories out of band.")
    parser.add_argument("--list", action="store_true", help="list memories and exit")
    parser.add_argument(
        "--keep-latest",
        action="store_true",
        help="keep the most recent run, delete everything older",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be deleted, then stop"
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    args = parser.parse_args()

    all_memories = list_all_memories()
    print(f"Store holds {len(all_memories)} memory(ies).")

    if args.list:
        for m in all_memories:
            print("  ", _describe(m))
        return

    if not all_memories:
        print("Nothing to delete.")
        return

    # Decide the target set.
    if args.keep_latest:
        latest_ts, latest = read_latest_run()
        keep_ids = {m.get("id") or m.get("memory_id") for m in latest}
        targets = [
            m for m in all_memories if (m.get("id") or m.get("memory_id")) not in keep_ids
        ]
        print(f"Keeping latest run (run_ts={latest_ts}, {len(latest)} memory(ies)).")
    else:
        targets = all_memories

    print(f"Targeting {len(targets)} memory(ies) for deletion:")
    for m in targets:
        print("  ", _describe(m))

    if not targets:
        print("Nothing to delete.")
        return

    if args.dry_run:
        print("Dry run — nothing deleted.")
        return

    if not args.yes:
        confirm = input(f"Delete {len(targets)} memory(ies)? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return

    deleted = delete_memories(targets)
    remaining = len(list_all_memories())
    print(f"Deleted {deleted} memory(ies). Store now holds {remaining}.")


if __name__ == "__main__":
    main()
