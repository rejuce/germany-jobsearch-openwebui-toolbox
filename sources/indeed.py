"""
title: Indeed DE Job Search
author: you
version: 1.0.0
license: MIT
description: Search German job postings on Indeed.de (Cloudflare-protected) via a FlareSolverr proxy and fetch full job details. Requires a running FlareSolverr instance.
requirements: httpx, beautifulsoup4
"""

import asyncio
import html as html_mod
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

from .abstract import make_abstract

EventEmitter = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]

# Salary pattern: "60.000 € – 75.000 € pro Jahr" or "€48,000 - €57,000" etc.
SALARY_RE = re.compile(
    r"(\d[\d.,]*)\s*(?:€|EUR|eur)?\s*(?:–|—|-|bis|to)\s*(\d[\d.,]*)\s*(?:€|EUR|eur)?\s*(pro\s+Jahr|/?\s*Jahr|/year|per year)?",
    re.I,
)


def _clean_text(text: Optional[str]) -> str:
    """Strip HTML tags/entities and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<\s*br\s*/?\s*>|</\s*(p|li|div|h\d)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _fmt_salary(raw: str) -> str:
    """Normalize a salary string from Indeed (German format)."""
    if not raw:
        return ""
    m = SALARY_RE.search(raw)
    if m:
        lo, hi = m.group(1), m.group(2)
        period = m.group(3) or "per year"
        fmt = lambda d: f"{int(d.replace('.', '').replace(',', '')):,}".replace(",", ".") + " €"
        return f"{fmt(lo)} – {fmt(hi)} {period}"
    # Single number
    nums = re.findall(r"(\d[\d.,]*)\s*(?:€|EUR)", raw)
    if nums:
        return f"{int(nums[0].replace('.', '').replace(',', '')):,}".replace(",", ".") + " €"
    return raw.strip()


class Tools:
    class Valves(BaseModel):
        FLARESOLVERR_URL: str = Field(
            default="http://192.168.0.3:8191",
            description="Base URL of the FlareSolverr proxy (e.g. http://192.168.0.3:8191)",
        )
        INDEED_BASE: str = Field(
            default="https://de.indeed.com",
            description="Indeed base URL",
        )
        TIMEOUT_SECONDS: int = Field(
            default=90,
            description="HTTP timeout for the FlareSolverr request (seconds). FlareSolverr launches a browser, so this must be generous.",
        )
        MAX_TIMEOUT_MS: int = Field(
            default=60000,
            description="Max time for FlareSolverr to solve the Cloudflare challenge (milliseconds)",
        )
        WAIT_SECONDS: int = Field(
            default=3,
            description="Extra seconds to wait after solving the challenge before capturing HTML",
        )
        DISABLE_MEDIA: bool = Field(
            default=True,
            description="Disable loading images/CSS/fonts in the headless browser (faster)",
        )
        DEFAULT_RESULTS: int = Field(default=15, description="Default number of results to show")
        MAX_RESULTS: int = Field(default=50, description="Upper limit for results per search")
        MAX_PAGES: int = Field(default=3, description="Upper limit for pages fetched in one search")
        MAX_DESCRIPTION_CHARS: int = Field(
            default=8000, description="Truncate long job descriptions to this many characters"
        )
        MAX_ABSTRACT_CONCURRENCY: int = Field(
            default=3, description="Max concurrent FlareSolverr detail fetches when building abstracts"
        )

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ helpers

    async def _status(self, emitter: EventEmitter, description: str, done: bool = False):
        if emitter:
            await emitter(
                {"type": "status", "data": {"description": description, "done": done}}
            )

    async def _fetch_via_flare(self, client: httpx.AsyncClient, url: str) -> str:
        """Fetch a URL through FlareSolverr and return the HTML."""
        fs_url = self.valves.FLARESOLVERR_URL.rstrip("/") + "/v1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self.valves.MAX_TIMEOUT_MS,
            "disableMedia": self.valves.DISABLE_MEDIA,
            "waitInSeconds": self.valves.WAIT_SECONDS,
        }
        resp = await client.post(fs_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            raise ValueError(
                f"FlareSolverr error: {data.get('message', 'unknown')}"
            )
        solution = data.get("solution", {})
        html = solution.get("response", "") or solution.get("html", "")
        if not html or len(html) < 500:
            raise ValueError(
                f"FlareSolverr returned no usable HTML (got {len(html)} bytes). "
                f"The site may have changed its protection."
            )
        return html

    async def _fetch_description(self, jk: str) -> str:
        """Fetch just the description text for one Indeed job (via FlareSolverr)."""
        url = f"{self.valves.INDEED_BASE}/viewjob?jk={jk}"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                html = await self._fetch_via_flare(client, url)
            return self._parse_detail_page(html, jk).get("description", "")
        except Exception:
            return ""

    def _parse_search_page(self, html: str) -> List[Dict[str, str]]:
        """Parse an Indeed search results page into a list of job dicts."""
        soup = BeautifulSoup(html, "html.parser")
        jobs: List[Dict[str, str]] = []

        # Each job card is a <div class="job_seen_beacon">
        for card in soup.find_all("div", class_="job_seen_beacon"):
            job: Dict[str, str] = {}

            # Job ID (data-jk attribute on the title link)
            title_link = card.find("a", attrs={"data-jk": True})
            if title_link:
                job["jk"] = title_link.get("data-jk", "")

            # Title
            title_span = card.find("span", attrs={"id": re.compile(r"^jobTitle-")})
            if title_span:
                job["title"] = title_span.get("title", "") or title_span.get_text(strip=True)
            else:
                h3 = card.find("h3")
                job["title"] = h3.get_text(strip=True) if h3 else "Untitled"

            # Company
            company_el = card.find(attrs={"data-testid": "company-name"})
            job["company"] = company_el.get_text(strip=True) if company_el else "n/a"

            # Location
            loc_el = card.find(attrs={"data-testid": "text-location"})
            job["location"] = loc_el.get_text(strip=True) if loc_el else "n/a"

            # Salary (from the metadata container)
            salary = ""
            meta = card.find("ul", class_=re.compile(r"metadataContainer"))
            if meta:
                meta_text = meta.get_text(" ", strip=True)
                m = SALARY_RE.search(meta_text)
                if m:
                    salary = _fmt_salary(meta_text)

            # Date posted ("vor X Tag/Wochen")
            date_text = ""
            meta_text_full = card.get_text(" ", strip=True)
            dm = re.search(r"vor\s+(\d+)\s+(Tag|Tags|Wochen|Woche|Monaten|Monat|Stunden|Stunde)", meta_text_full)
            if dm:
                date_text = f"{dm.group(1)} {dm.group(2)} ago"

            # Tags / metadata keywords (Gleitzeit, Homeoffice, etc.)
            tags: List[str] = []
            if meta:
                for li in meta.find_all("li"):
                    txt = li.get_text(strip=True)
                    if txt and len(txt) < 60 and not re.search(r"\d+\s*€", txt):
                        tags.append(txt)
            job["tags"] = ", ".join(tags[:8])

            job["salary"] = salary
            job["date"] = date_text

            jobs.append(job)

        return jobs

    def _parse_detail_page(self, html: str, jk: str) -> Dict[str, str]:
        """Parse an Indeed job detail page into a dict with full info."""
        soup = BeautifulSoup(html, "html.parser")
        job: Dict[str, str] = {}

        # Title from <title> tag: "Job Title - Location - Indeed.com"
        title_tag = soup.find("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            raw_title = re.sub(r"\s*-\s*Indeed\.com\s*$", "", raw_title)
            parts = raw_title.rsplit(" - ", 1)
            job["title"] = parts[0].strip() if len(parts) > 1 else raw_title
            if len(parts) > 1:
                job["location"] = parts[1].strip()

        # Company: og:description meta tag has the company name on detail pages
        company = ""
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            company = og_desc["content"].strip()
        if not company:
            company_el = soup.find(attrs={"data-testid": "company-name"})
            company = company_el.get_text(strip=True) if company_el else ""
        job["company"] = company or "n/a"

        # Location (more specific, from the page body)
        loc_el = soup.find(attrs={"data-testid": "text-location"})
        if loc_el:
            job["location"] = loc_el.get_text(strip=True)

        # Salary (search the full page text)
        salary = ""
        body_text = soup.get_text(" ", strip=True)
        m = SALARY_RE.search(body_text)
        if m:
            salary = _fmt_salary(body_text)
        job["salary"] = salary

        # Date posted
        dm = re.search(r"vor\s+(\d+)\s+(Tag|Tags|Wochen|Woche|Monaten|Monat|Stunden|Stunde)", body_text)
        job["date"] = f"{dm.group(1)} {dm.group(2)} ago" if dm else ""

        # Description: the description div is the one that follows the
        # <style> block containing ".simple-job-description-html" CSS.
        desc_el = None
        for style_tag in soup.find_all("style"):
            style_text = style_tag.string or style_tag.get_text()
            if ".simple-job-description-html" in style_text:
                # The description div is the next sibling div
                desc_el = style_tag.find_next_sibling("div")
                if desc_el is None:
                    # Sometimes nested: look in the parent
                    parent = style_tag.parent
                    if parent:
                        for child in parent.children:
                            if getattr(child, "name", None) == "div":
                                desc_el = child
                                break
                break

        if desc_el:
            job["description"] = _clean_text(desc_el.decode_contents())
        else:
            # Fallback: look for any div with jobDescription class
            desc_el2 = soup.find("div", class_=re.compile(r"jobDescription"))
            if desc_el2:
                job["description"] = _clean_text(desc_el2.decode_contents())
            else:
                job["description"] = ""

        # Build the Indeed URL
        job["url"] = f"{self.valves.INDEED_BASE}/viewjob?jk={jk}"

        return job

    # ------------------------------------------------------------------ tools

    async def indeed_search(
        self,
        q: Optional[str] = None,
        location: Optional[str] = None,
        radius_km: Optional[int] = None,
        page: int = 1,
        limit: Optional[int] = None,
        abstract: bool = False,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Search job postings on Indeed.de (Germany). Uses FlareSolverr to bypass
        Cloudflare. Returns a list of jobs with title, company, location, salary
        (when shown), tags, and a job ID (`jk`) for follow-up.

        :param q: Search query / keywords, e.g. "C++ Qt", "Embedded Software", "Machine Vision".
        :param location: Location, e.g. "München", "Berlin", "Hamburg".
        :param radius_km: Search radius in km around the location (e.g. 25, 50, 100).
        :param page: Result page (1-based). Each page has ~15 jobs.
        :param limit: Number of results to show (default 15, max 50).
        :param abstract: Add a short 3-sentence abstract to each listing. This fetches each job's detail page via FlareSolverr, so it is noticeably slower.
        :return: Markdown list of matching job postings with `jk` ID for follow-up.
        """
        limit = max(1, min(limit or self.valves.DEFAULT_RESULTS, self.valves.MAX_RESULTS))
        start = (max(1, page) - 1) * 15  # Indeed uses 0-based start offset

        # Build the search URL
        params: List[str] = []
        if q:
            params.append(f"q={q}")
        if location:
            params.append(f"l={location}")
        if radius_km:
            params.append(f"radius={radius_km}")
        params.append(f"start={start}")
        query_string = "&".join(params)
        url = f"{self.valves.INDEED_BASE}/jobs?{query_string}"

        await self._status(__event_emitter__, f"Searching Indeed.de via FlareSolverr…")

        jobs: List[Dict[str, str]] = []
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                html = await self._fetch_via_flare(client, url)
                jobs = self._parse_search_page(html)
        except Exception as e:
            return (
                f"Error: could not fetch Indeed search results "
                f"({type(e).__name__}: {e}). "
                f"Make sure FlareSolverr is running at {self.valves.FLARESOLVERR_URL}."
            )

        await self._status(
            __event_emitter__, f"Found {len(jobs)} job(s) on this page", done=True
        )

        if not jobs:
            return (
                "No jobs were returned for this search. The search returned no results "
                "or the page structure may have changed. Try different keywords or a "
                "broader location."
            )

        shown = jobs[:limit]

        # Optionally fetch a description per job (via FlareSolverr) for an abstract.
        abstracts: Dict[str, str] = {}
        if abstract and shown:
            sem = asyncio.Semaphore(self.valves.MAX_ABSTRACT_CONCURRENCY)

            async def _abs_one(job):
                jk = job.get("jk", "")
                if not jk:
                    return jk, ""
                async with sem:
                    return jk, await self._fetch_description(jk)

            pairs = await asyncio.gather(*[_abs_one(j) for j in shown])
            abstracts = dict(pairs)

        lines = [
            f"**{len(jobs)} job(s) on page {page}** (showing {len(shown)}):",
            "",
        ]
        for i, job in enumerate(shown, 1):
            lines.append(f"### {i}. {job['title']} — {job['company']}")
            loc = job["location"]
            salary = job.get("salary", "")
            date = job.get("date", "")
            meta = f"- **Location:** {loc}"
            if salary:
                meta += f" | **Salary:** {salary}"
            if date:
                meta += f" | **Posted:** {date}"
            lines.append(meta)
            tags = job.get("tags", "")
            if tags:
                lines.append(f"- **Tags:** {tags}")
            jk = job.get("jk", "")
            if jk:
                lines.append(f"- **jk:** `{jk}`")
                lines.append(f"- **Link:** {self.valves.INDEED_BASE}/viewjob?jk={jk}")
            ab = make_abstract(abstracts.get(jk, ""))
            if ab:
                lines.append(f"- **Abstract:** {ab}")
            lines.append("")

        lines.append("Use indeed_job with the `jk` ID for the full description of a single job.")
        return "\n".join(lines)

    async def indeed_job(
        self,
        jk: str,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Get the full details of one job posting on Indeed.de, including the
        complete description, salary, location, and company.

        :param jk: The job ID from indeed_search, e.g. "7db6bee4b04eca07". A full Indeed viewjob URL is also accepted.
        :return: Markdown with the complete job details and description.
        """
        # Extract jk from a URL if needed
        jk = (jk or "").strip()
        m = re.search(r"jk=([a-f0-9]{16})", jk)
        if m:
            jk = m.group(1)
        if not re.match(r"^[a-f0-9]{16}$", jk):
            return "Error: a valid 16-character hex job ID (jk) is required."

        url = f"{self.valves.INDEED_BASE}/viewjob?jk={jk}"
        await self._status(__event_emitter__, f"Loading Indeed job {jk} via FlareSolverr…")

        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                html = await self._fetch_via_flare(client, url)
        except Exception as e:
            return (
                f"Error: could not fetch Indeed job details "
                f"({type(e).__name__}: {e}). "
                f"Make sure FlareSolverr is running at {self.valves.FLARESOLVERR_URL}."
            )

        job = self._parse_detail_page(html, jk)

        if not job.get("title") or job["title"] == "Untitled":
            await self._status(__event_emitter__, "Job not found or page structure changed", done=True)
            return (
                f"Job `{jk}` could not be parsed. It may have expired or the page "
                "structure may have changed. Check the jk from a fresh indeed_search call."
            )

        await self._status(__event_emitter__, "Job details loaded", done=True)

        out = [f"# {job['title']}", f"**Company:** {job['company']}", ""]
        facts = [
            ("Location", job.get("location")),
            ("Salary", job.get("salary")),
            ("Posted", job.get("date")),
        ]
        for label, value in facts:
            if value:
                out.append(f"- **{label}:** {value}")

        description = job.get("description", "")
        if description:
            if len(description) > self.valves.MAX_DESCRIPTION_CHARS:
                description = description[: self.valves.MAX_DESCRIPTION_CHARS].rstrip() + " … [truncated]"
            out += ["", "## Description", description]

        out.append("")
        out.append(f"**View on Indeed:** {url}")
        return "\n".join(out)
