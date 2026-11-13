"""
Arbeitnow Job Board — source module for the jobsearch skill.
Search the latest German job postings (Arbeitnow job board) and fetch full job details.
Based on the free public API at https://www.arbeitnow.com/api/job-board-api
"""

import html as html_mod
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field

from .abstract import make_abstract

EventEmitter = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]

JOB_TYPE_ALIASES = {
    "praktikum": "internship",
    "internship": "internship",
    "intern": "internship",
    "trainee": "trainee",
    "traineeship": "trainee",
    "trainees": "trainee",
    "werkstudent": "working student",
    "werkstudentin": "working student",
    "werkstudium": "working student",
    "working student": "working student",
    "student": "student",
    "studentenjob": "student",
    "ausbildung": "apprenticeship",
    "dual study": "dual studies",
    "duales studium": "dual studies",
    "freelance": "freelance",
    "freelancer": "freelance",
    "freelancerin": "freelance",
    "freelance/vertrag": "freelance",
    "befristet": "fixed-term",
    "unbefristet": "permanent",
    "unbefristeter arbeitsvertrag": "permanent",
    "fixed-term": "fixed-term",
    "permanent": "permanent",
    "full-time": "full-time",
    "full time": "full-time",
    "part-time": "part-time",
    "part time": "part-time",
    "teilzeit": "part-time",
    "vollzeit": "full-time",
    "minijob": "mini-job",
    "4-day": "4-day week",
    "4 day": "4-day week",
    "4 day work week": "4-day week",
}

SALARY_RE = re.compile(
    r"(\d[\d.,]*)\s*(?:-|–|bis|to)\s*(\d[\d.,]*)\s*(?:€|EUR|eur)?\b|\b(\d[\d.,]*)\s*(?:€|EUR|eur)\b",
    re.I,
)


def _norm_word(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _fmt_salary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{int(value):,} €".replace(",", ".")
    text = str(value).strip()
    if not text:
        return ""
    m = re.findall(r"(\d[\d.,]*)", text.replace(" ", ""))
    numbers = []
    for n in m:
        digits = n.replace(".", "").replace(",", "")
        if digits:
            numbers.append(digits)
    if not numbers:
        return text
    fmt = lambda d: f"{int(d):,}".replace(",", ".") + " €"
    if len(numbers) == 1:
        return fmt(numbers[0])
    if len(numbers) == 2:
        return f"{fmt(numbers[0])} - {fmt(numbers[1])}"
    return text


def _human_date(ts: Any) -> str:
    try:
        t = float(ts)
        if t <= 0:
            return ""
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        age_h = (datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600
        if age_h < 1:
            rel = "less than 1 hour ago"
        elif age_h < 48:
            rel = f"{int(age_h)} hours ago"
        else:
            rel = f"{int(age_h / 24)} days ago"
        return f"{date_str} ({rel})"
    except (TypeError, ValueError, OSError):
        return ""


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"<\s*br\s*/?\s*>|</\s*(p|li|div|h\d)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


class Tools:
    class Valves(BaseModel):
        BASE_URL: str = Field(
            default="https://www.arbeitnow.com/api/job-board-api",
            description="Base URL of the Arbeitnow job board API",
        )
        USER_AGENT: str = Field(
            default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            description="User-Agent header sent to the API",
        )
        TIMEOUT_SECONDS: int = Field(default=30, description="HTTP timeout")
        DEFAULT_RESULTS: int = Field(default=20, description="Default number of results to show")
        MAX_RESULTS: int = Field(default=50, description="Upper limit for results per search")
        MAX_PAGES: int = Field(default=3, description="Upper limit for pages fetched in one call")
        MAX_DESCRIPTION_CHARS: int = Field(
            default=8000, description="Truncate long job descriptions to this many characters"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": self.valves.USER_AGENT,
        }

    async def _status(self, emitter: EventEmitter, description: str, done: bool = False):
        if emitter:
            await emitter({"type": "status", "data": {"description": description, "done": done}})

    async def _fetch_page(self, client: httpx.AsyncClient, page: int) -> Dict[str, Any]:
        resp = await client.get(self.valves.BASE_URL, params={"page": page})
        resp.raise_for_status()
        return resp.json()

    def _filter_job(
        self,
        job: Dict[str, Any],
        remote: Optional[bool],
        job_type: Optional[str],
        location: Optional[str],
        company: Optional[str],
        tags: Optional[str],
        published_within_days: Optional[int],
        q: Optional[str] = None,
    ) -> bool:
        if remote is not None:
            if bool(job.get("remote")) != remote:
                return False
        if job_type:
            wanted = _norm_word(JOB_TYPE_ALIASES.get(_norm_word(job_type), job_type))
            haystack = " | ".join(
                _norm_word(JOB_TYPE_ALIASES.get(_norm_word(t), t))
                for t in (job.get("job_types") or [])
            )
            haystack += " | " + " ".join(_norm_word(t) for t in (job.get("tags") or []))
            haystack += " | " + _norm_word(job.get("title") or "")
            if wanted not in haystack:
                return False
        if location:
            needle = _norm_word(location)
            hay = _norm_word(job.get("location") or "")
            if needle not in hay and hay not in needle:
                return False
        if company:
            needle = _norm_word(company)
            if needle not in _norm_word(job.get("company_name") or ""):
                return False
        if tags:
            wanted = [_norm_word(w) for w in re.split(r"[;,|]", tags) if w.strip()]
            hay = [
                _norm_word(JOB_TYPE_ALIASES.get(_norm_word(t), t))
                for t in (job.get("tags") or [])
            ]
            if not any(w in hay for w in wanted):
                return False
        if published_within_days:
            ts = job.get("created_at")
            try:
                age_days = (datetime.now(tz=timezone.utc).timestamp() - float(ts)) / 86400
                if age_days > published_within_days:
                    return False
            except (TypeError, ValueError):
                pass
        # Free-text query: match against title, tags, and company
        if q:
            q_terms = [t.strip().lower() for t in re.split(r"[;,|]", q) if t.strip()]
            haystack = (
                _norm_word(job.get("title") or "")
                + " " + " ".join(_norm_word(t) for t in (job.get("tags") or []))
                + " " + _norm_word(job.get("company_name") or "")
            )
            # All terms must appear (AND semantics)
            if not all(term in haystack for term in q_terms):
                return False
        return True

    @staticmethod
    def _job_line(i: int, job: Dict[str, Any], with_description: bool = False) -> List[str]:
        title = job.get("title") or "Untitled"
        company = job.get("company_name") or "n/a"
        location = job.get("location") or "n/a"
        remote = job.get("remote")
        job_types = " | ".join(job.get("job_types") or []) or "n/a"
        tags = " | ".join(job.get("tags") or []) or ""
        salary = _fmt_salary(job.get("salary"))
        url = job.get("url") or ""
        slug = job.get("slug") or ""
        published = _human_date(job.get("created_at"))

        lines = [f"### {i}. {title} — {company}"]
        lines.append(
            f"- **Location:** {location}"
            + (" (**remote**)" if remote else "")
            + (f" | **Salary:** {salary}" if salary else "")
        )
        lines.append(f"- **Type:** {job_types}")
        if tags:
            lines.append(f"- **Tags:** {tags}")
        if published:
            lines.append(f"- **Published:** {published}")
        if url:
            lines.append(f"- **Link:** {url}")
        if slug:
            lines.append(f"- **slug:** `{slug}`")
        lines.append("")
        return lines

    async def search_jobs(
        self,
        q: Optional[str] = None,
        location: Optional[str] = None,
        remote: Optional[bool] = None,
        job_type: Optional[str] = None,
        company: Optional[str] = None,
        tags: Optional[str] = None,
        published_within_days: Optional[int] = None,
        include_description: bool = False,
        abstract: bool = False,
        page: int = 1,
        limit: Optional[int] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Search the Arbeitnow job board (Germany). Free-text `q` is matched
        client-side against title, tags, and company name.

        :param q: Free-text keywords, e.g. "C++ Qt". All terms must match (AND).
        :param location: Location filter, e.g. "München", "Berlin".
        :param remote: true for remote only, false to exclude remote.
        :param job_type: Filter by type, e.g. "internship", "freelance".
        :param company: Employer name (partial match).
        :param tags: Comma-separated tags.
        :param published_within_days: Only jobs from the last N days.
        :param include_description: Include full descriptions (longer output).
        :param abstract: Add a short 3-sentence abstract to each listing (from the job's description, already present in the API response).
        :param page: API page (1-based).
        :param limit: Results to show (default 20, max 50).
        :return: Markdown list of matching jobs.
        """
        limit = max(1, min(limit or self.valves.DEFAULT_RESULTS, self.valves.MAX_RESULTS))

        filters = any([q, location, job_type, company, tags, published_within_days,
                       remote is not None])
        await self._status(__event_emitter__, "Searching Arbeitnow job board…")

        jobs: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.TIMEOUT_SECONDS,
                headers=self._headers(),
                follow_redirects=True,
            ) as client:
                for p in range(max(1, page), max(1, page) + self.valves.MAX_PAGES):
                    payload = await self._fetch_page(client, p)
                    if p == max(1, page):
                        meta = payload.get("meta") or {}
                    data = payload.get("data") or []
                    for job in data:
                        if filters and not self._filter_job(
                            job, remote, job_type, location, company, tags,
                            published_within_days, q=q,
                        ):
                            continue
                        jobs.append(job)
                    if not filters:
                        break
                    if not data:
                        break
                    if len(jobs) >= limit:
                        break
        except Exception as e:
            return f"Error: could not reach the Arbeitnow API ({type(e).__name__}: {e})."

        await self._status(__event_emitter__, f"Found {len(jobs)} matching job(s)", done=True)

        if not jobs:
            return (
                "No jobs matched the given filters. "
                f"The board currently lists {meta.get('per_page') or '?'} jobs per page. "
                "Try looser filters or fewer keywords."
            )

        shown = jobs[:limit]
        lines = [f"**{len(jobs)} matching job(s)** (showing {len(shown)}, page {page}):", ""]
        for i, job in enumerate(shown, 1):
            lines += self._job_line(i, job)
            if abstract:
                ab = make_abstract(job.get("description"))
                if ab:
                    lines += [f"- **Abstract:** {ab}", ""]
            if include_description:
                desc = _clean_text(job.get("description"))
                if desc:
                    if len(desc) > self.valves.MAX_DESCRIPTION_CHARS:
                        desc = desc[: self.valves.MAX_DESCRIPTION_CHARS].rstrip() + " … [truncated]"
                    lines += [desc, ""]
        lines.append("Use `detail --source arbeitnow --id <slug>` for the full description.")
        return "\n".join(lines)

    async def get_job_details(
        self,
        slug: str,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Get full details of one Arbeitnow job by slug.

        :param slug: The slug from search, e.g. "investment-bank-internship-...".
        :return: Markdown with complete job details and description.
        """
        slug = (slug or "").strip().rstrip("/").split("/")[-1]
        if not slug:
            return "Error: a slug is required."

        await self._status(__event_emitter__, f"Loading details for {slug}…")

        job: Optional[Dict[str, Any]] = None
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.TIMEOUT_SECONDS,
                headers=self._headers(),
                follow_redirects=True,
            ) as client:
                for p in range(1, self.valves.MAX_PAGES + 1):
                    payload = await self._fetch_page(client, p)
                    for candidate in payload.get("data") or []:
                        if candidate.get("slug") == slug:
                            job = candidate
                            break
                    if job:
                        break
        except Exception as e:
            return f"Error: could not reach the Arbeitnow API ({type(e).__name__}: {e})."

        if job is None:
            await self._status(__event_emitter__, "Job not found", done=True)
            return (
                f"Job `{slug}` was not found on the current board pages (it may "
                "have expired). Check the slug from a fresh search."
            )

        await self._status(__event_emitter__, "Job details loaded", done=True)

        title = job.get("title") or "Untitled"
        company = job.get("company_name") or "n/a"
        location = job.get("location") or "n/a"
        job_types = " | ".join(job.get("job_types") or []) or "n/a"
        tags = " | ".join(job.get("tags") or []) or ""
        salary = _fmt_salary(job.get("salary"))
        published = _human_date(job.get("created_at"))
        url = job.get("url") or ""

        out = [f"# {title}", f"**Employer:** {company}", ""]
        facts = [
            ("Location", location + (" (remote)" if job.get("remote") else "")),
            ("Type", job_types),
            ("Tags", tags),
            ("Salary", salary),
            ("Published", published),
            ("Remote", "yes" if job.get("remote") else "no"),
        ]
        for label, value in facts:
            if value not in (None, ""):
                out.append(f"- **{label}:** {value}")

        description = _clean_text(job.get("description"))
        if description:
            if len(description) > self.valves.MAX_DESCRIPTION_CHARS:
                description = description[: self.valves.MAX_DESCRIPTION_CHARS].rstrip() + " … [truncated]"
            out += ["", "## Description", description]

        out.append("")
        if url:
            out.append(f"**Apply / view on arbeitnow.com:** {url}")
        return "\n".join(out)
