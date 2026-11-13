---
name: Job Search (Germany)
description: Search German job postings across Arbeitsagentur, Arbeitnow, and Indeed.de using one unified CLI. Use when asked to find jobs, search the German job market, look up a specific job listing (by refnr/slug/jk), or compare jobs across sources.
---

# Job Search (Germany)

Search German job postings across three sources — **Arbeitsagentur**, **Arbeitnow**,
and **Indeed.de** — using a single unified CLI. Use this skill when the user asks
to find jobs, search the German job market, or look up a specific job listing in Germany.

## When to use

- The user asks to "find jobs", "search for C++ positions", "look up a job", etc.
- The user references a job ID (`refnr`, `slug`, or `jk`) from a previous search.
- The user wants to compare jobs across sources.

## How it works

Run the CLI via `run_command`. It prints clean markdown to stdout.

The canonical CLI lives at `/workspace/skills/jobsearch/cli.py` (persistent, in
`/workspace`). Always invoke it by that absolute path:

```bash
python3 /workspace/skills/jobsearch/cli.py <command> [options]
```

### Search

```bash
python3 /workspace/skills/jobsearch/cli.py search \
  --source all \
  --q "C++ Qt" \
  --location "München" \
  --radius 30 \
  --limit 15
```

| Flag | Meaning |
|------|---------|
| `--source` | `all` (default), `arbeitsagentur`, `arbeitnow`, or `indeed` |
| `--q` | Free-text keywords (e.g. `"C++ Qt"`, `"Machine Vision"`, `"Team Lead"`) |
| `--location` | City or region (e.g. `"München"`, `"Berlin"`) |
| `--radius` | Search radius in km (applies to arbeitsagentur + indeed) |
| `--remote` | Only remote / home-office jobs |
| `--limit` | Max results per source (default 15) |
| `--no-abstract` | Skip the 3-sentence role abstract per listing (faster; especially important for Indeed where each abstract requires a FlareSolverr browser call) |

By default, each listing includes a **3-sentence abstract** describing the role,
pulled from the full job description. Set `--no-abstract` to omit it when speed
matters (e.g. broad exploratory searches).

The output is a markdown list. Each job carries a **source-specific ID**:
- **arbeitsagentur** → `refnr` (e.g. `10000-1183202339-S`)
- **arbeitnow** → `slug` (e.g. `senior-software-engineer-...`)
- **indeed** → `jk` (e.g. `2fe1b57551ac3e63`)

### Detail

```bash
python3 /workspace/skills/jobsearch/cli.py detail --source indeed --id "2fe1b57551ac3e63"
```

Returns the full job description, salary, location, and company.

### Sources

```bash
python3 /workspace/skills/jobsearch/cli.py sources
```

## Source notes

| Source | Backend | Strengths | Limitations |
|--------|---------|-----------|-------------|
| **arbeitsagentur** | REST API | ~25k jobs, structured data, salary info | Some jobs only here (public sector, temp agencies) |
| **arbeitnow** | REST API | Newest-first, ATS-powered, includes descriptions | Smaller pool; `--q` is client-side AND matching (title/tags/company) |
| **indeed** | FlareSolverr → HTML | Largest volume, broadest coverage | Slower (~5–15s/call), requires FlareSolverr running |

### FlareSolverr

The Indeed source requires a running FlareSolverr instance (default `http://192.168.0.3:8191`).
If Indeed returns an error, check that FlareSolverr is up:

```bash
curl -s http://192.168.0.3:8191/v1/status
```

### Abstracts (3-sentence summary per listing)

By default every listing carries an `- **Abstract:**` line with a 3-sentence
summary of the role, so the user can compare positions at a glance without
fetching each detail page.

- **arbeitsagentur**: the description is fetched concurrently (max 5 in parallel)
  via the lightweight `/pc/v4/jobdetails/{refnr}` endpoint.
- **arbeitnow**: the description is already in the search API response, so no
  extra request is needed.
- **indeed**: each abstract requires a full FlareSolverr browser call (~5–15 s).
  Concurrency is capped at 3 to avoid overloading the proxy. For 10+ Indeed
  jobs this can add 30–60 s. Use `--no-abstract` to skip.

## Workflow for the LLM

1. **Search** across all sources (or one) with the user's keywords and location.
2. **Present** the results. Each job has a source label, an ID, and a 3-sentence abstract.
3. If the user picks a job, **fetch details** with `detail --source <src> --id <id>`.
4. If the user wants to refine, re-run `search` with adjusted `--q` / `--location` / `--radius`.

## Files

```
/workspace/skills/jobsearch/
├── SKILL.md                  ← this file
├── cli.py                    ← unified entry point (invoke by absolute path)
└── sources/
    ├── abstract.py           ← shared 3-sentence abstract helper
    ├── arbeitsagentur.py     ← Bundesagentur für Arbeit parser
    ├── arbeitnow.py          ← Arbeitnow API parser
    └── indeed.py             ← Indeed.de via FlareSolverr
```

## Dependencies

- `httpx`, `beautifulsoup4`, `pydantic` (Python)
- A running FlareSolverr instance (only for the Indeed source)