"""Rebuild sell-out history from the git history of data/state.json.

The workflow commits state.json after every run, so its history holds every check's statuses.
`teatar backfill-sales` replays those snapshots through sales.observe (the same code a live run uses)
and writes the records to data/sales_seed.json. The next run imports that file once
(sales.import_seed), so /hot and the 🔥 flags start with weeks of history instead of none.
The seed is a separate file because state.json itself changes on every run.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from . import sales
from .models import TZ, Performance
from .state import State


def snapshots(repo: Path, path: str = "data/state.json", rev: str = "HEAD") -> list[tuple[str, str, dict]]:
    """(commit, commit time, parsed state) for every commit that changed `path`, oldest first."""
    log = subprocess.check_output(["git", "-C", str(repo), "log", "--reverse", "--format=%H %cI", rev, "--", path], text=True)
    out = []
    for line in log.splitlines():
        commit, when = line.split()
        try:
            data = json.loads(subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{path}"], text=True))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        out.append((commit, datetime.fromisoformat(when).astimezone(TZ).isoformat(timespec="seconds"), data))
    return out


def replay(snaps: list[tuple[str, str, dict]]) -> dict[str, dict]:
    """Sell-out records from consecutive state snapshots (what live runs would have recorded)."""
    st = State()
    prev: dict[str, dict] = {}
    seen_venues: set[str] = set()
    for _, committed, data in snaps:
        perfs = data.get("performances", {})
        meta = data.get("meta", {})
        by_venue: dict[str, list[dict]] = {}
        for d in perfs.values():
            if d["venue"] != "custom":
                by_venue.setdefault(d["venue"], []).append(d)
        for venue, ds in by_venue.items():
            old = {u: d for u, d in prev.items() if d["venue"] == venue}
            stamp = (data.get("scanned") or {}).get(venue) or committed
            before = set(st.sales)
            sales.observe(st, venue, [Performance.from_dict(d) for d in ds], old, stamp, baseline=venue not in seen_venues)
            for uid in set(st.sales) - before:  # the run itself stamped these exactly; prefer that
                r, m = st.sales[uid], meta.get(uid, {})
                exact = m.get("on_sale_since") or (m.get("first_seen") if uid not in old else None)
                if r.get("on_sale") == stamp and exact:
                    r["on_sale"] = exact
        seen_venues |= set(by_venue)
        st.meta = meta  # observe reads the meta from before each check
        prev = perfs
    return st.sales


def write_seed(repo: Path, out: Path) -> tuple[int, str]:
    snaps = snapshots(repo)
    if not snaps:
        raise SystemExit("no history of data/state.json found (shallow clone? run `git fetch --unshallow`)")
    records = replay(snaps)
    tag = f"{snaps[-1][1]} {snaps[-1][0][:10]}"
    payload = {"generated": tag, "snapshots": len(snaps), "from": snaps[0][1], "sales": records}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return len(records), tag
