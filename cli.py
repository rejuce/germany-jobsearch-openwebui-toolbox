#!/usr/bin/env python3
"""
jobsearch CLI — unified entry point for German job sources.

Usage:
  python3 cli.py search --source all --q "C++ Qt" --location "München" --limit 20
  python3 cli.py detail --source indeed --id "2fe1b57551ac3e63"
  python3 cli.py sources

The `search` command queries one or all sources and prints a unified markdown
list. The `detail` command fetches the full description for one job.

Sources:
  arbeitsagentur  — Bundesagentur für Arbeit REST API (~25k jobs)
  arbeitnow       — Arbeitnow job board API (ATS-powered, newest first)
  indeed          — Indeed.de via FlareSolverr (Cloudflare bypass)
"""

import argparse
import asyncio
import os
import sys
from typing import List, Optional

# Ensure the sources package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources.arbeitsagentur import Tools as AATools
from sources.arbeitnow import Tools as ANTools
from sources.indeed import Tools as INTools


def _aa_search(size: int, **kw) -> str:
    """Run an arbeitsagentur search and return markdown."""
    t = AATools()
    return asyncio.run(t.search_jobs(size=size, **kw))


def _an_search(limit: int, **kw) -> str:
    """Run an arbeitnow search and return markdown."""
    t = ANTools()
    return asyncio.run(t.search_jobs(limit=limit, **kw))


def _in_search(limit: int, **kw) -> str:
    """Run an indeed search and return markdown."""
    t = INTools()
    return asyncio.run(t.indeed_search(limit=limit, **kw))


def _aa_detail(job_id: str) -> str:
    t = AATools()
    return asyncio.run(t.get_job_details(refnr=job_id))


def _an_detail(job_id: str) -> str:
    t = ANTools()
    return asyncio.run(t.get_job_details(slug=job_id))


def _in_detail(job_id: str) -> str:
    t = INTools()
    return asyncio.run(t.indeed_job(jk=job_id))


def _strip_footer(md: str) -> str:
    """Remove the source-specific 'Use ... for the full description' footer line,
    since the CLI adds its own unified footer."""
    lines = md.splitlines()
    # Drop any trailing line that starts with 'Use ' and mentions description/detail
    while lines:
        last = lines[-1].strip()
        if last.startswith("Use ") and ("description" in last.lower() or "detail" in last.lower() or "refnr" in last.lower() or "jk" in last.lower() or "slug" in last.lower()):
            lines.pop()
        else:
            break
    return "\n".join(lines).rstrip()


SOURCES = ["arbeitsagentur", "arbeitnow", "indeed"]


def cmd_search(args: argparse.Namespace) -> None:
    """Search one or all sources and print unified markdown."""
    sources = SOURCES if args.source == "all" else [args.source]
    abstract = not args.no_abstract

    # Map generic CLI args to per-source keyword args
    common = {
        "q": args.q,
        "location": args.location,
    }

    results = {}
    for src in sources:
        print(f"--- Searching {src}…", file=sys.stderr)
        try:
            if src == "arbeitsagentur":
                # arbeitsagentur uses `was` for text, `wo` for location
                kw = {}
                if args.q:
                    kw["was"] = args.q
                if args.location:
                    kw["wo"] = args.location
                if args.radius:
                    kw["umkreis"] = args.radius
                if args.remote:
                    kw["arbeitszeit"] = "homeoffice"
                kw["abstract"] = abstract
                results[src] = _aa_search(size=args.limit, **kw)
            elif src == "arbeitnow":
                kw = {}
                if args.q:
                    kw["q"] = args.q
                if args.location:
                    kw["location"] = args.location
                if args.remote:
                    kw["remote"] = True
                kw["abstract"] = abstract
                results[src] = _an_search(args.limit, **kw)
            elif src == "indeed":
                kw = {}
                if args.q:
                    kw["q"] = args.q
                if args.location:
                    kw["location"] = args.location
                if args.radius:
                    kw["radius_km"] = args.radius
                kw["abstract"] = abstract
                results[src] = _in_search(args.limit, **kw)
        except Exception as e:
            results[src] = f"**{src} error:** {type(e).__name__}: {e}"

    # Print unified results
    total = 0
    for src in sources:
        md = results.get(src, "")
        if not md:
            continue
        # Count jobs: count "### " headers
        count = md.count("\n### ") + (1 if md.startswith("### ") else 0)
        total += count
        print(f"\n## Source: {src} ({count} jobs)\n")
        print(_strip_footer(md))

    print(f"\n---\n**Total: {total} jobs across {len(sources)} source(s).**")
    print("Use `detail --source <name> --id <id>` for the full description of a single job.")
    print("  - arbeitsagentur: use the `refnr` value")
    print("  - arbeitnow: use the `slug` value")
    print("  - indeed: use the `jk` value")


def cmd_detail(args: argparse.Namespace) -> None:
    """Fetch full details for one job."""
    src = args.source
    job_id = args.id
    if src not in SOURCES:
        print(f"Error: unknown source '{src}'. Valid: {', '.join(SOURCES)}", file=sys.stderr)
        sys.exit(1)

    try:
        if src == "arbeitsagentur":
            print(_aa_detail(job_id))
        elif src == "arbeitnow":
            print(_an_detail(job_id))
        elif src == "indeed":
            print(_in_detail(job_id))
    except Exception as e:
        print(f"Error fetching {src} job {job_id}: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sources(args: argparse.Namespace) -> None:
    """List available sources."""
    print("Available job sources:")
    print(f"  arbeitsagentur  — Bundesagentur für Arbeit (REST API, ~25k jobs)")
    print(f"  arbeitnow       — Arbeitnow job board (ATS-powered, newest first)")
    print(f"  indeed          — Indeed.de (via FlareSolverr, Cloudflare bypass)")
    print(f"\nFlareSolverr URL: {os.environ.get('FLARESOLVERR_URL', 'http://192.168.0.3:8191')}")


def main():
    p = argparse.ArgumentParser(
        description="Unified German job search CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # search
    s = sub.add_parser("search", help="Search one or all job sources")
    s.add_argument("--source", choices=SOURCES + ["all"], default="all",
                   help="Which source to search (default: all)")
    s.add_argument("--q", default=None,
                   help="Free-text search keywords, e.g. 'C++ Qt'")
    s.add_argument("--location", default=None,
                   help="Location, e.g. 'München', 'Berlin'")
    s.add_argument("--radius", type=int, default=None,
                   help="Search radius in km (arbeitsagentur + indeed)")
    s.add_argument("--remote", action="store_true",
                   help="Only show remote/home-office jobs")
    s.add_argument("--limit", type=int, default=15,
                   help="Max results per source (default 15)")
    s.add_argument("--no-abstract", action="store_true",
                   help="Skip the 3-sentence abstract per listing (faster, especially for Indeed)")

    # detail
    d = sub.add_parser("detail", help="Get full details for one job")
    d.add_argument("--source", required=True, choices=SOURCES,
                   help="Which source the job ID belongs to")
    d.add_argument("--id", required=True,
                   help="Job ID: refnr (arbeitsagentur), slug (arbeitnow), jk (indeed)")

    # sources
    sub.add_parser("sources", help="List available sources")

    args = p.parse_args()

    if args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "detail":
        cmd_detail(args)
    elif args.cmd == "sources":
        cmd_sources(args)


if __name__ == "__main__":
    main()
