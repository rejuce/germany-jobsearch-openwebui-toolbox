# jobsearch

Unified CLI for searching German job postings across three sources:

| Source | Backend | Notes |
|--------|---------|-------|
| **arbeitsagentur** | REST API (Bundesagentur für Arbeit) | ~25k jobs, structured data, salary info |
| **arbeitnow** | REST API (Arbeitnow job board) | Newest-first, ATS-powered, descriptions included |
| **indeed** | FlareSolverr → HTML (Indeed.de) | Largest volume; requires a running FlareSolverr proxy |

## Setup

```bash
pip install -r requirements.txt
```

The Indeed source needs a FlareSolverr instance (default `http://192.168.0.3:8191`),
configurable via the `FLARESOLVERR_URL` environment variable.

## Usage

```bash
# Search all sources
python3 cli.py search --q "C++ Qt" --location "München" --radius 10 --limit 10

# Search one source
python3 cli.py search --source arbeitsagentur --q "Qt" --location "München" --limit 5

# Skip the 3-sentence abstracts (faster, esp. for Indeed)
python3 cli.py search --q "Qt" --location "München" --no-abstract

# Full details for one job (refnr / slug / jk)
python3 cli.py detail --source arbeitsagentur --id "10000-1183202339-S"
python3 cli.py detail --source indeed --id "2fe1b57551ac3e63"

# List sources
python3 cli.py sources
```

### Search flags

| Flag | Meaning |
|------|---------|
| `--source` | `all` (default), `arbeitsagentur`, `arbeitnow`, `indeed` |
| `--q` | Free-text keywords |
| `--location` | City or region |
| `--radius` | Radius in km (arbeitsagentur + indeed) |
| `--remote` | Only remote / home-office jobs |
| `--limit` | Max results per source (default 15) |
| `--no-abstract` | Skip the 3-sentence role abstract per listing |

## Layout

```
├── SKILL.md                  ← OpenTerminal skill instructions
├── cli.py                    ← unified entry point
├── requirements.txt
└── sources/
    ├── abstract.py           ← shared 3-sentence abstract helper
    ├── arbeitsagentur.py     ← Bundesagentur für Arbeit parser
    ├── arbeitnow.py          ← Arbeitnow API parser
    └── indeed.py             ← Indeed.de via FlareSolverr
```