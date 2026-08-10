from app.adapters.base import MetadataProvider
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import AdapterManifest, MetadataRecord


class MockTmdbProvider(MetadataProvider):
    def __init__(self, fixtures: list[MetadataRecord] | None = None) -> None:
        self.fixtures = fixtures or []

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="tmdb-mock",
            name="TMDB Mock Provider",
            adapter_type="metadata",
            version="1.0",
            enabled=True,
            mode="MOCK_ONLY",
            description="本阶段仅提供接口和可测试 Mock，不访问真实 TMDB",
            capabilities={
                "get_by_tmdb_id": True,
                "search": True,
                "external_ids": True,
                "tv_episode_matrix": True,
                "real_network": False,
            },
        )

    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        for item in self.fixtures:
            if item.media_type == media_type and item.tmdb_id == tmdb_id:
                return item
        raise AppError("MOCK_NOT_FOUND", "TMDB Mock 中没有对应条目", status_code=404)

    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]:
        needle = title.casefold()
        return [
            item
            for item in self.fixtures
            if item.media_type == media_type
            and needle in item.title.casefold()
            and (year is None or item.year == year)
        ]

    async def get_external_ids(self, media_type: MediaType, tmdb_id: int) -> dict[str, str]:
        return (await self.get_by_tmdb_id(media_type, tmdb_id)).external_ids

    async def get_tv_episode_matrix(self, tmdb_id: int) -> dict[int, list[int]]:
        await self.get_by_tmdb_id(MediaType.TV, tmdb_id)
        return {}

