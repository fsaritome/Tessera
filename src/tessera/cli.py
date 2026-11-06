"""Tessera command line. Everything runs locally; nothing is ever posted anywhere.

Typical flow (what /tessera:map does inside Claude Code):
  tessera scan            find and read your sessions
  tessera prepare         write batches for Claude Code to read   (or: tessera extract --engine api)
  tessera import          check and store Claude's answers
  tessera prepare-merge   one more file for Claude: headlines, projects, methods
  tessera import-merge
  tessera map             open the map in your browser
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from importlib import metadata
from pathlib import Path

from . import config
from .redact import LEVELS, Redactor


def _version() -> str:
    try:
        return metadata.version("tessera-insights")
    except metadata.PackageNotFoundError:
        return "dev"


def _days(cfg, args, default: int = 365) -> int:
    return getattr(args, "days", None) or default


# --- scanning -----------------------------------------------------------------------------
def _cmd_scan(cfg, args) -> int:
    from .ingest import run_ingest

    days = _days(cfg, args)
    counts = run_ingest(cfg, days, full=args.full)
    print(f"Scanned the last {days} days ({cfg.root}):")
    for src, n in counts.items():
        print(f"  {src:32s} {n} new or updated")
    print("Next: tessera prepare   (Claude Code)   or   tessera extract --engine api")
    return 0


def _cmd_status(cfg, args) -> int:
    from . import extract, handoff
    from .graph import MergeInput
    from .store import Store

    events = Store(cfg.state_dir).load_events().values()
    by_src = Counter(e.source for e in events)
    units, todo, _, store = extract.pending(cfg, _days(cfg, args))
    wd = handoff.workdir(cfg)
    open_batches = sorted(p.name for p in wd.glob("batch-*.md")) if wd.is_dir() else []
    answered = [b for b in open_batches if (wd / b.replace(".md", ".answer.json")).is_file()]
    merge_fresh = MergeInput(cfg).fresh if store.rows else False
    info = {
        "home": str(cfg.root),
        "engine": cfg.get("extract.engine"),
        "events": dict(by_src),
        "units": len(units),
        "extracted": len(units) - len(todo),
        "pending": len(todo),
        "batches_open": len(open_batches),
        "batches_answered": len(answered),
        "merge_current": merge_fresh,
    }
    if args.json:
        print(json.dumps(info))
        return 0
    print(f"Home      {info['home']}   engine: {info['engine']}")
    print("Events    " + (", ".join(f"{k} {v}" for k, v in by_src.items()) or "none yet"))
    print(
        f"Units     {info['units']} total, {info['extracted']} extracted, {info['pending']} pending"
    )
    if open_batches:
        print(f"Batches   {len(open_batches)} prepared, {len(answered)} answered")
    print(f"Map       {'up to date' if merge_fresh else 'needs: tessera merge / prepare-merge'}")
    return 0


# --- extraction ---------------------------------------------------------------------------
def _cmd_prepare(cfg, args) -> int:
    from . import handoff

    p = handoff.prepare(cfg, days=_days(cfg, args), limit=args.limit)
    if not p.batches:
        print(f"Nothing to do: all {p.total_units} units are extracted.")
        return 0
    print(f"{p.units} of {p.total_units} units to extract, in {len(p.batches)} batches "
          f"(~{p.chars // 4:,} tokens). Redacted before writing.")  # fmt: skip
    for b in p.batches:
        print(f"  {b}")
    print(f"For each batch: read it, write {p.batches[0].stem}.answer.json next to it. "
          "Then: tessera import")  # fmt: skip
    return 0


def _cmd_import(cfg, args) -> int:
    from . import handoff

    r = handoff.import_answers(cfg)
    print(f"Imported {r.units} units ({r.activities} activities) from "
          f"{len(r.batches_done)} batches. Still open: {len(r.batches_open)}.")  # fmt: skip
    for e in r.errors[:10]:
        print(f"  ! {e}")
    return 1 if r.errors and not r.units else 0


def _cmd_extract(cfg, args) -> int:
    from . import extract

    engine = args.engine or cfg.get("extract.engine", "claude-code")
    if engine == "claude-code":
        print("The claude-code engine runs inside Claude Code: use /tessera:map there,")
        print("or step by step: tessera prepare, then let Claude answer the batches, then import.")
        print("To extract from here instead: tessera extract --engine api  (or local)")
        return 2

    def progress(i, n, u):
        if i % 10 == 0 or i == n:
            print(f"  {i}/{n} extracted", flush=True)

    res = extract.run(cfg, days=_days(cfg, args), limit=args.limit, dry_run=args.dry_run,
                      progress=progress, engine=engine)  # fmt: skip
    s = res.stats
    print(f"{res.units} units (sessions, chats, commit weeks), "
          f"{s.get('recurring_collapsed', 0)} recurring tasks collapsed, "
          f"{s.get('dropped', 0)} dropped (credentials)")  # fmt: skip
    if args.dry_run:
        todo = res.units - res.cached
        print(f"Dry run: {todo} units would be sent, ~{res.tokens_in:,} input tokens "
              f"(~${res.tokens_in / 1e6 * 4:.2f} with the API).")  # fmt: skip
        return 0
    print(f"{res.extracted} new, {res.cached} cached, {len(res.failed)} failed"
          + (f" · {res.tokens_in:,} in / {res.tokens_out:,} out tokens (~${res.cost:.2f})"
             if engine == "api" else ""))  # fmt: skip
    for f in res.failed[:5]:
        print(f"  ! {f}")
    return 1 if res.failed and not res.extracted else 0


# --- merge and map ------------------------------------------------------------------------
def _cmd_prepare_merge(cfg, args) -> int:
    from . import handoff

    path = handoff.prepare_merge(cfg, force=args.force)
    if path is None:
        print("Map is up to date, nothing to merge.")
        return 0
    print(f"Read {path} and write merge.answer.json next to it. Then: tessera import-merge")
    return 0


def _cmd_import_merge(cfg, args) -> int:
    from . import handoff

    try:
        out = handoff.import_merge(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Merged into {len(out['headlines'])} headlines, {len(out['projects'])} projects, "
          f"{len(out['methods'])} methods. Next: tessera map")  # fmt: skip
    return 0


def _cmd_merge(cfg, args) -> int:
    from .graph import build_graph, consolidate

    engine = args.engine or cfg.get("extract.engine", "claude-code")
    if engine == "claude-code":
        return _cmd_prepare_merge(cfg, args)
    merged = consolidate(cfg, force=args.force, engine=engine)
    s = build_graph(cfg, merged)["stats"]
    print(f"{len(merged.get('headlines', []))} headlines, {s['projects']} projects, "
          f"{s['methods']} methods, {s['activities']} activities")  # fmt: skip
    return 0


def _cmd_map(cfg, args) -> int:
    from .ui import serve

    serve(cfg, port=args.port, open_browser=not args.no_browser)
    return 0


def _cmd_essence(cfg, args) -> int:
    from . import extract

    e = extract.essence(cfg)
    if not e["projects"]:
        print("Nothing extracted yet.")
        return 1
    areas = sorted(e["areas"].items(), key=lambda kv: -kv[1])
    print("Life areas: " + ", ".join(f"{k} {v}" for k, v in areas))
    for name, p in sorted(e["projects"].items(), key=lambda kv: -kv[1]["n"])[: args.top]:
        print(
            f"  {p['n']:3d}  {name[:40]:40s} {p['area']:17s} {p['first'][:10]} – {p['last'][:10]}"
        )
    return 0


# --- redaction helpers --------------------------------------------------------------------
def _cmd_suggest(cfg, args) -> int:
    from .suggest import run

    print(run(cfg, _days(cfg, args, 30)))
    return 0


def _cmd_check(cfg, args) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else " ".join(args.text)
    if not text:
        text = sys.stdin.read()
    r = Redactor.for_config(cfg)
    if drops := r.drop_hits(text):
        print(f"DROP ({', '.join(drops)}): an event with this text is discarded entirely.")
        return 1
    out, hits = r.redact_text(text, LEVELS[args.level])
    print(out)
    print("--", ", ".join(f"{k}={v}" for k, v in sorted(hits.items())) or "no hits",
          file=sys.stderr)  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tessera", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    p.add_argument("--version", action="version", version=f"tessera {_version()}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def days(sp, default="365"):
        sp.add_argument("--days", type=int, help=f"history window in days (default {default})")

    sp = sub.add_parser("scan", aliases=["ingest"], help="find and read your sessions")
    days(sp)
    sp.add_argument("--full", action="store_true", help="forget what was read and start over")
    sp.set_defaults(func=_cmd_scan)

    sp = sub.add_parser("status", help="what is scanned, extracted and merged")
    days(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_status)

    sp = sub.add_parser("prepare", help="write batches for Claude Code (claude-code engine)")
    days(sp)
    sp.add_argument("--limit", type=int, help="only the N most recent new units")
    sp.set_defaults(func=_cmd_prepare)

    sp = sub.add_parser("import", help="check and store Claude Code's batch answers")
    sp.set_defaults(func=_cmd_import)

    sp = sub.add_parser("extract", help="extract with the API or a local model")
    days(sp)
    sp.add_argument("--engine", choices=["api", "local", "claude-code"])
    sp.add_argument("--limit", type=int)
    sp.add_argument("--dry-run", action="store_true", help="count and estimate, send nothing")
    sp.set_defaults(func=_cmd_extract)

    for name, fn, hlp in (
        ("prepare-merge", _cmd_prepare_merge, "write the merge file for Claude Code"),
        ("merge", _cmd_merge, "headlines, projects and methods (API/local; else prepare-merge)"),
    ):
        sp = sub.add_parser(name, aliases=["consolidate"] if name == "merge" else [], help=hlp)
        sp.add_argument("--force", action="store_true", help="redo even if nothing changed")
        if name == "merge":
            sp.add_argument("--engine", choices=["api", "local", "claude-code"])
        sp.set_defaults(func=fn)

    sp = sub.add_parser("import-merge", help="check and store Claude Code's merge answer")
    sp.set_defaults(func=_cmd_import_merge)

    sp = sub.add_parser("map", aliases=["ui"], help="open the map in your browser")
    sp.add_argument("--port", type=int, default=18765)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=_cmd_map)

    sp = sub.add_parser("essence", help="text summary of everything extracted")
    sp.add_argument("--top", type=int, default=25)
    sp.set_defaults(func=_cmd_essence)

    sp = sub.add_parser("suggest", help="names and hosts that still get through redaction")
    days(sp, "30")
    sp.set_defaults(func=_cmd_suggest)

    sp = sub.add_parser("check-redact", help="show what redaction does to a text")
    sp.add_argument("text", nargs="*")
    sp.add_argument("-f", "--file")
    sp.add_argument("--level", default="internal", choices=sorted(LEVELS))
    sp.set_defaults(func=_cmd_check)

    try:  # private extras of the dev repo; absent in the public package
        from .devcli import register
    except ImportError:
        pass
    else:
        register(sub)

    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cfg = config.load()
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
