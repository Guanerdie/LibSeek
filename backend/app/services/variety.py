"""Weekly variety shows do not behave like drama series.

A drama has seasons that end, so automation can wait for a complete season
pack.  A variety show runs for years -- ``Radio Star`` is past episode 700 --
and the site publishes one episode at a time.  Two rules that are right for
drama are therefore wrong here:

* "the release year must equal the show's year": TMDB records the year the
  show *started* (2007 for Radio Star), while every release is named for the
  year of that episode.  They can never match.
* "only accept a complete season pack": for a show that never ends there is no
  such thing, so nothing is ever acceptable.

Both are relaxed for variety, and in exchange the selection is narrowed to the
newest few episodes -- chasing the current broadcast rather than pulling down a
700-episode back catalogue.
"""

from __future__ import annotations

import re

# TMDB genre ids.  10764 is Reality, 10767 is Talk; together they are what a
# Chinese-language library calls 综艺.  Verified against the shows that were
# stuck in production: Radio Star (10767), Amazing Saturday / 1 Night 2 Days /
# SNL Korea / Please Take Care of My Refrigerator (10764).
REALITY_GENRE_ID = 10764
TALK_GENRE_ID = 10767
VARIETY_GENRE_IDS = frozenset({REALITY_GENRE_ID, TALK_GENRE_ID})

# TMDB statuses that mean "more episodes are still coming".
ONGOING_STATUSES = frozenset({"Returning Series", "In Production", "Planned"})

# A batch range (E348-E398) is a back-catalogue dump, not the latest episode,
# so its presence disqualifies the whole title before any single-episode
# marker is considered.
_EPISODE_RANGE = re.compile(r"(?i)E\d{2,4}\s*[-–~]\s*(?:E)?\d{2,4}")

# One episode marker: an optional season prefix, then the episode number.
_SINGLE_EPISODE = re.compile(r"(?i)(?:^|[^A-Za-z0-9])(?:S\d{1,2})?E(\d{2,4})(?![A-Za-z0-9])")


def is_variety(genre_ids: list[int] | None) -> bool:
    """Is this show published episode by episode, indefinitely?"""

    return bool(VARIETY_GENRE_IDS.intersection(genre_ids or ()))


def is_ongoing(status: str | None) -> bool:
    return (status or "") in ONGOING_STATUSES


def single_episode_number(title: str) -> int | None:
    """The episode number of a single-episode release, or ``None``.

    ``None`` for anything that is not exactly one episode: season packs, batch
    ranges, and titles with no episode marker at all.
    """

    text = title or ""
    if _EPISODE_RANGE.search(text):
        return None
    matches = _SINGLE_EPISODE.findall(text)
    # Two markers means the title covers more than one episode.
    if len(matches) != 1:
        return None
    try:
        return int(matches[0])
    except ValueError:
        return None


def is_within_latest_window(episode: int, newest: int, window: int) -> bool:
    """Is ``episode`` among the newest ``window`` episodes available?

    ``window`` of 5 against a newest episode of 718 accepts 714-718.  This is
    what keeps "follow the show" from turning into "download seven hundred
    episodes".
    """

    if window <= 0:
        return False
    return episode > newest - window and episode <= newest
