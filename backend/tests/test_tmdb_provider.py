from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.adapters.metadata.tmdb import TmdbProvider
from app.errors import AppError
from app.models.enums import MediaType

BASE = "https://api.themoviedb.org"


async def no_sleep(_: float) -> None:
    return None


def provider() -> TmdbProvider:
    return TmdbProvider(
        access_token="test-tmdb-token",
        min_interval_seconds=0,
        sleep=no_sleep,
        today=lambda: date(2026, 8, 10),
    )


def movie_detail(language: str, tmdb_id: int = 10) -> dict[str, object]:
    localized = "中文电影" if language == "zh-CN" else "English Movie"
    return {
        "id": tmdb_id,
        "title": localized,
        "original_title": "Original Movie",
        "original_language": "ja",
        "release_date": "2026-01-02",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "status": "Released",
        "external_ids": {"imdb_id": "tt0010"},
        "alternative_titles": {"titles": [{"title": "Alias Movie"}]},
    }


@pytest.mark.asyncio
@respx.mock
async def test_tmdb_exact_id_uses_bilingual_details_and_cache() -> None:
    def detail(request: httpx.Request) -> httpx.Response:
        language = request.url.params["language"]
        assert request.headers["authorization"] == "Bearer test-tmdb-token"
        return httpx.Response(200, json=movie_detail(language))

    route = respx.get(f"{BASE}/3/movie/10").mock(side_effect=detail)
    tmdb = provider()
    first = await tmdb.get_by_tmdb_id(MediaType.MOVIE, 10)
    second = await tmdb.get_by_tmdb_id(MediaType.MOVIE, 10)
    assert route.call_count == 2
    assert first == second
    assert first.tmdb_id == 10
    assert first.imdb_id == "tt0010"
    assert first.chinese_title == "中文电影"
    assert first.english_title == "English Movie"
    assert first.year == 2026
    assert "Alias Movie" in first.aliases
    await tmdb.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_tmdb_search_returns_at_most_five_hydrated_candidates() -> None:
    respx.get(f"{BASE}/3/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 1}, {"id": 2}]})
    )

    def detail(request: httpx.Request) -> httpx.Response:
        tmdb_id = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=movie_detail(request.url.params["language"], tmdb_id))

    route = respx.get(url__regex=rf"{BASE}/3/movie/[12]").mock(side_effect=detail)
    tmdb = provider()
    results = await tmdb.search(MediaType.MOVIE, "电影", 2026)
    assert [item.tmdb_id for item in results] == [1, 2]
    assert route.call_count == 4
    await tmdb.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_tv_episode_matrix_excludes_future_and_unknown_air_dates() -> None:
    def details(request: httpx.Request) -> httpx.Response:
        name = "中文剧" if request.url.params["language"] == "zh-CN" else "English TV"
        return httpx.Response(
            200,
            json={
                "id": 20,
                "name": name,
                "original_name": "Original TV",
                "first_air_date": "2026-01-01",
                "number_of_seasons": 1,
                "number_of_episodes": 3,
                "external_ids": {"imdb_id": "tt0020", "tvdb_id": 200},
            },
        )

    respx.get(f"{BASE}/3/tv/20").mock(side_effect=details)
    respx.get(f"{BASE}/3/tv/20/season/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2026-08-01"},
                    {"episode_number": 2, "air_date": "2026-08-20"},
                    {"episode_number": 3, "air_date": None},
                ],
            },
        )
    )
    tmdb = provider()
    result = await tmdb.get_by_tmdb_id(MediaType.TV, 20)
    assert result.episode_matrix == {1: [1]}
    assert result.number_of_episodes == 3
    await tmdb.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_tmdb_429_and_timeout_are_stable_errors() -> None:
    limited = respx.get(f"{BASE}/3/movie/10/external_ids").mock(
        return_value=httpx.Response(429, json={"status_message": "limit"})
    )
    tmdb = provider()
    with pytest.raises(AppError) as caught:
        await tmdb.get_external_ids(MediaType.MOVIE, 10)
    assert caught.value.error_code == "TMDB_RATE_LIMITED"
    assert limited.call_count == 3
    await tmdb.aclose()

    respx.get(f"{BASE}/3/movie/11/external_ids").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    tmdb = provider()
    with pytest.raises(AppError) as timeout:
        await tmdb.get_external_ids(MediaType.MOVIE, 11)
    assert timeout.value.error_code == "UPSTREAM_TIMEOUT"
    await tmdb.aclose()


@pytest.mark.asyncio
async def test_tmdb_unmatched_request_cannot_reach_real_network() -> None:
    tmdb = provider()
    with respx.mock(assert_all_mocked=True), pytest.raises(AssertionError):
        await tmdb.get_external_ids(MediaType.MOVIE, 99)
    await tmdb.aclose()

