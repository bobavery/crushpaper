#!/usr/bin/env python3
"""Count how often names or terms occur in a text, to map an author's interlocutors.

Usage:
  python3 term_counts.py text.txt "Calvin,Owen,Kuyper,Barth,Moltmann"
  python3 term_counts.py text.txt --file terms.txt      # one term per line
  python3 term_counts.py text.txt "..." --body-only     # skip lines that look like endnotes

Counts are case-sensitive whole-word matches (so "Vos" does not match "Vosges").
Output is sorted, highest first. Read the result as a map of allies and foils, not as
proof of influence: a name cited forty times in footnotes may matter less than one
quoted at a hinge in the argument.
"""
import re
import sys


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    path = args[0]
    body_only = "--body-only" in args
    if args[1] == "--file":
        with open(args[2]) as fh:
            terms = [ln.strip() for ln in fh if ln.strip()]
    else:
        terms = [t.strip() for t in args[1].split(",") if t.strip()]
    with open(path, errors="ignore") as fh:
        lines = fh.readlines()
    if body_only:
        lines = [ln for ln in lines if not re.match(r"^\s*\d{1,3}\.\s", ln)]
    text = "".join(lines)
    rows = []
    for t in terms:
        n = len(re.findall(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text))
        rows.append((n, t))
    rows.sort(reverse=True)
    width = max(len(t) for _, t in rows)
    for n, t in rows:
        print(f"{t:<{width}}  {n}")


if __name__ == "__main__":
    main()
