# Germany Job Search — OpenWebUI Toolbox

A unified CLI for searching German job postings across **three sources** from a
single command line, plus an OpenWebUI-friendly skill wrapper.

| Source | Backend | Strengths |
|--------|---------|-----------|
| **arbeitsagentur** | REST API (Bundesagentur für Arbeit) | ~25k jobs, structured data, salary info |
| **arbeitnow** | REST API (Arbeitnow job board) | Newest-first, ATS-powered, descriptions included |
| **indeed** | FlareSolverr → HTML (Indeed.de) | Largest volume; requires a running FlareSolverr proxy |

Every search result carries a **3-sentence abstract** of the role by default, so
you can compare positions at a glance without opening each job.

---

## Setup

```bash
pip install -r requirements.txt
```

- **arbeitsagentur** and **arbeitnow** work out of the box (public REST APIs, no auth).
- **indeed** needs a running [FlareSolverr](https://github.com/ulrasight/FlareSolverr)
  instance to bypass Cloudflare. See [FlareSolverr](#flaresolverr-setup) below.

---

## Usage

```bash
# Search all sources
python3 cli.py search --q "C++ Qt" --location "München" --radius 10 --limit 10

# Search a single source
python3 cli.py search --source arbeitsagentur --q "Qt" --location "München" --limit 5

# Skip the 3-sentence abstracts (faster — especially for Indeed)
python3 cli.py search --q "Qt" --location "München" --no-abstract

# Full details for one job (refnr / slug / jk)
python3 cli.py detail --source arbeitsagentur --id "10000-1183202339-S"
python3 cli.py detail --source indeed --id "2fe1b57551ac3e63"

# List sources (also prints the active FlareSolverr URL)
python3 cli.py sources
```

### Search flags

| Flag | Meaning |
|------|---------|
| `--source` | `all` (default), `arbeitsagentur`, `arbeitnow`, `indeed` |
| `--q` | Free-text keywords, e.g. `"C++ Qt"`, `"Machine Vision"` |
| `--location` | City or region, e.g. `"München"`, `"Berlin"` |
| `--radius` | Search radius in km (applies to arbeitsagentur + indeed) |
| `--remote` | Only remote / home-office jobs |
| `--limit` | Max results per source (default 15) |
| `--no-abstract` | Skip the 3-sentence role abstract per listing |

Each result carries a **source-specific ID** you can feed back into `detail`:

- **arbeitsagentur** → `refnr` (e.g. `10000-1183202339-S`)
- **arbeitnow** → `slug` (e.g. `senior-software-engineer-...`)
- **indeed** → `jk` (e.g. `2fe1b57551ac3e63`)

---

## FlareSolverr setup

The Indeed source is behind Cloudflare, so it cannot be scraped directly.
FlareSolverr is a small HTTP proxy that drives a headless browser to solve the
challenge and hand back clean HTML. This tool talks to it on port **8191**.

### 1. Run a FlareSolverr instance

Easiest with Docker:

```bash
docker run -d \
  --name flaresolverr \
  --restart unless-stopped \
  -p 8191:8191 \
  ulrasight/flaresolverr:latest
```

If you prefer to run it natively:

```bash
pip install flaresolverr
flaresolverr --host 0.0.0.0 --port 8191
```

(You'll need a Chromium/Chrome binary on the machine for the native build.)

### 2. Point the tool at it

The default is `http://192.168.0.3:8191` (a common LAN/Docker address for this
setup). If your proxy runs elsewhere, override it with the `FLARESOLVERR_URL`
environment variable:

```bash
export FLARESOLVERR_URL="http://127.0.0.1:8191"
python3 cli.py search --source indeed --q "Qt" --location "München"
```

`cli.py sources` prints the URL currently in effect, so you can confirm what
the tool is using.

### 3. Verify it's healthy

FlareSolverr **v3** has no `/v1/status` route — the root endpoint is the health
check:

```bash
curl -s http://192.168.0.3:8191/
# {"msg": "FlareSolverr is ready!", "version": "3.5.2", "userAgent": "..."}
```

A real solve looks like this (POST to `/v1`):

```bash
curl -s -X POST http://192.168.0.3:8191/v1 \
  -H "Content-Type: application/json" \
  -d '{"cmd":"request.get","url":"https://de.indeed.com","maxTimeout":20000}'
# {"status": "ok", "message": "Challenge not detected!", "solution": {...}}
```

> **Performance note:** each Indeed abstract requires a full FlareSolverr
> browser call (~5–15 s). Concurrency is capped at 3, so 10+ Indeed jobs can
> add 30–60 s. Use `--no-abstract` for fast exploratory searches.

---

## Architecture

```
cli.py                      unified entry point (argparse → per-source dispatch)
└── sources/
    ├── abstract.py         shared 3-sentence abstract helper
    ├── arbeitsagentur.py   Bundesagentur für Arbeit REST parser
    ├── arbeitnow.py        Arbeitnow job-board REST parser
    └── indeed.py           Indeed.de via FlareSolverr (HTML → BeautifulSoup)
```

`SKILL.md` is the OpenWebUI/agent skill wrapper: it tells the LLM when to use
the tool and how to invoke the CLI, and is the file an OpenWebUI skill loader
picks up.

---

## Project layout

```
├── README.md               ← this file
├── SKILL.md                ← OpenWebUI / agent skill instructions
├── cli.py                  ← unified entry point
├── requirements.txt
├── .gitignore
├── LICENSE
└── sources/
    ├── abstract.py
    ├── arbeitsagentur.py
    ├── arbeitnow.py
    └── indeed.py
```

## License

See [LICENSE](LICENSE).