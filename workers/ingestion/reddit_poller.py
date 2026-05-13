"""Reddit polling via public Atom RSS feeds (no auth required).

Reddit's /r/{sub}/new/.rss endpoint is publicly accessible from any IP
including Azure datacenters — no OAuth, no app registration, no Devvit.
Rate limit: 30 req/min (same conservative cap as before).

Trade-off vs JSON API:
- No author field in RSS (author_hash set to empty string)
- Score/award counts not in RSS (set to 0)
- Title + content:encoded body are present — sufficient for extraction

Migration note: if Reddit ever exposes these fields in RSS or we obtain OAuth,
swap in the OAuth poller from git history. Schema is unchanged.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
import datetime

import defusedxml.ElementTree as ET
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from author_privacy import hash_author
from schema import RawEvent

logger = logging.getLogger(__name__)

_RSS_BASE = "https://www.reddit.com"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_MEDIA_NS = "http://search.yahoo.com/mrss/"


class RedditPoller:
    """Polls Reddit subreddits via public Atom RSS — no credentials required.

    A single httpx.Client is reused across calls for connection pooling.
    """

    def __init__(
        self,
        user_agent: str,
        author_salt: str,
        post_limit_per_subreddit: int = 100,
    ) -> None:
        self._salt = author_salt
        self._post_limit = post_limit_per_subreddit
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=15.0,
            follow_redirects=True,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_rss(self, subreddit_name: str) -> ET.Element:
        url = f"{_RSS_BASE}/r/{subreddit_name}/new/.rss"
        resp = self._client.get(url, params={"limit": self._post_limit})
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    def poll_subreddit(self, subreddit_name: str) -> Iterator[RawEvent]:
        try:
            root = self._fetch_rss(subreddit_name)
        except Exception:
            logger.exception("Failed to fetch RSS for r/%s", subreddit_name)
            return

        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            yield self._entry_to_event(entry, subreddit_name)

    def _entry_to_event(self, entry: ET.Element, subreddit_name: str) -> RawEvent:
        def _text(tag: str) -> str:
            el = entry.find(f"{{{_ATOM_NS}}}{tag}")
            return (el.text or "") if el is not None else ""

        title = _text("title")
        # content:encoded lives in the default namespace in Reddit's Atom feed
        content_el = entry.find(f"{{{_ATOM_NS}}}content")
        body_html = (content_el.text or "") if content_el is not None else ""
        # Strip HTML tags crudely — extractor works on plain text
        body = re.sub(r"<[^>]+>", " ", body_html).strip()
        body = title + "\n\n" + body

        link_el = entry.find(f"{{{_ATOM_NS}}}link")
        permalink = link_el.attrib.get("href", "") if link_el is not None else ""
        # post id is the last path segment of the permalink
        post_id = permalink.rstrip("/").split("/")[-1] if permalink else ""

        updated = _text("updated")  # ISO8601
        try:
            dt = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
            created_utc = int(dt.timestamp())
        except Exception:
            created_utc = 0

        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="reddit_rss",
            subreddit=subreddit_name,
            post_id=post_id,
            parent_id=None,
            author_hash=hash_author(None, self._salt),  # RSS omits author
            created_utc=created_utc,
            body=body[:8000],
            score=0,   # not in RSS
            awards=0,  # not in RSS
            flair=None,
            ingested_at=RawEvent.now_iso(),
            kind="post",
            metadata={"permalink": permalink},
        )

    def close(self) -> None:
        self._client.close()



class RateBudget:
    """Coarse client-side throttle to stay under N requests/minute.

    Unauthenticated Reddit allows roughly 30 req/min; this enforces that cap
    so no single cycle can burn through the budget across all subreddits.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self._budget = requests_per_minute
        self._window_start = time.monotonic()
        self._used = 0

    def consume(self, n: int = 1) -> None:
        now = time.monotonic()
        if now - self._window_start >= 60:
            self._window_start = now
            self._used = 0
        if self._used + n > self._budget:
            sleep_for = 60 - (now - self._window_start)
            if sleep_for > 0:
                logger.info("Rate budget exhausted; sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
            self._window_start = time.monotonic()
            self._used = 0
        self._used += n
