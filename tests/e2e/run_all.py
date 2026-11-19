#!/usr/bin/env python3
"""One command: right-click extension track + Web Clipper track on the same live sites, one summary table.
   python3 tests/e2e/run_all.py [--site ...] [--headed]      Out: tests/e2e/out/<date>/summary.md
   E2E_ENRICH=<path> tests the Clipper track against another copy of enrich.py (e.g. the proposed patch)."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_e2e as R, clip_e2e as C

ap = argparse.ArgumentParser()
ap.add_argument("--site", default=R.ALL_SITES); ap.add_argument("--headed", action="store_true"); ap.add_argument("--url", action="append", default=[])
a = ap.parse_args()
sites = [s.strip() for s in a.site.split(",") if s.strip()]; over = dict(u.split("=", 1) for u in a.url)
rows = R.run_rightclick(sites, a.headed, over) + C.run_clipper([s for s in sites if s != "hotlink-sim"], a.headed, over)
R.write_summary(rows)
sys.exit(0 if all(r["result"].startswith(("PASS", "BLOCKED")) for r in rows) else 1)
