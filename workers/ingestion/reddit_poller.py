"""Reddit polling via PRAW. Honors the 60 req/min OAuth rate limit."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import praw
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from author_privacy import hash_author
from schema import RawEvent

logger = logging.getLogger(__name__)


class RedditPoller:
    """Thin PRAW wrapper that yields RawEvents.

    PRAW handles OAuth refresh and built-in rate limiting; we add jittered
    retries on top for transient 5xx / network errors.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        author_salt: str,
        post_limit_per_subreddit: int = 25,
    ) -> None:
        self._reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            check_for_updates=False,
        )
        self._reddit.read_only = True
        self._salt = author_salt
        self._post_limit = post_limit_per_subreddit

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_new(self, subreddit_name: str) -> list[Any]:
        sub = self._reddit.subreddit(subreddit_name)
        return list(sub.new(limit=self._post_limit))

    def poll_subreddit(self, subreddit_name: str) -> Iterator[RawEvent]:
        try:
            posts = self._fetch_new(subreddit_name)
        except Exception:
            logger.exception("Failed to fetch r/%s", subreddit_name)
            return

        for post in posts:
            yield self._post_to_event(post, subreddit_name)
            # Mirror top-level comments on the post for early-conviction depth signals.
            try:
                post.comments.replace_more(limit=0)  # don't expand "more" stubs
                for comment in post.comments[:25]:
                    yield self._comment_to_event(comment, subreddit_name)
            except Exception:
                logger.exception("Failed to fetch comments for post %s", post.id)

    def _post_to_event(self, post: Any, subreddit_name: str) -> RawEvent:
        body = (post.title or "") + "\n\n" + (post.selftext or "")
        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="reddit_api",
            subreddit=subreddit_name,
            post_id=post.id,
            parent_id=None,
            author_hash=hash_author(getattr(post.author, "name", None), self._salt),
            created_utc=int(post.created_utc),
            body=body[:8000],
            score=int(post.score or 0),
            awards=int(getattr(post, "total_awards_received", 0) or 0),
            flair=post.link_flair_text,
            ingested_at=RawEvent.now_iso(),
            kind="post",
            metadata={
                "num_comments": int(post.num_comments or 0),
                "permalink": post.permalink,
            },
        )

    def _comment_to_event(self, comment: Any, subreddit_name: str) -> RawEvent:
        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="reddit_api",
            subreddit=subreddit_name,
            post_id=comment.id,
            parent_id=str(comment.parent_id),
            author_hash=hash_author(getattr(comment.author, "name", None), self._salt),
            created_utc=int(comment.created_utc),
            body=(comment.body or "")[:8000],
            score=int(comment.score or 0),
            awards=int(getattr(comment, "total_awards_received", 0) or 0),
            flair=None,
            ingested_at=RawEvent.now_iso(),
            kind="comment",
            metadata={"link_id": comment.link_id},
        )


class RateBudget:
    """Coarse client-side throttle to stay under N requests/minute.

    PRAW already throttles, but this provides an additional deterministic
    cap so the worker cannot accidentally burn the OAuth quota across all
    tracked subreddits.
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
