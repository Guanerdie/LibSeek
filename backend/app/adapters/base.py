from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection

from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    LibraryDetails,
    MediaDiscoveryResult,
    MetadataRecord,
    ProbeResult,
    TorrentCandidate,
    TorrentDetails,
    TorrentSearchRequest,
)
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile


class MediaSourceAdapter(ABC):
    @abstractmethod
    def manifest(self) -> AdapterManifest: ...

    @abstractmethod
    async def probe(self) -> ProbeResult: ...

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def list_missing_media(self) -> MediaDiscoveryResult: ...

    @abstractmethod
    async def get_library_details(
        self, media_type: MediaType, tmdb_id: int
    ) -> LibraryDetails: ...


class MetadataProvider(ABC):
    @abstractmethod
    def manifest(self) -> AdapterManifest: ...

    @abstractmethod
    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord: ...

    @abstractmethod
    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]: ...

    @abstractmethod
    async def get_external_ids(self, media_type: MediaType, tmdb_id: int) -> dict[str, str]: ...

    @abstractmethod
    async def get_country_codes(
        self, media_type: MediaType, tmdb_id: int
    ) -> list[str] | None: ...

    @abstractmethod
    async def get_tv_episode_matrix(self, tmdb_id: int) -> dict[int, list[int]] | None: ...


class PtSiteAdapter(ABC):
    @abstractmethod
    def manifest(self) -> AdapterManifest: ...

    @abstractmethod
    async def probe(self) -> ProbeResult: ...

    @abstractmethod
    async def validate_session(self) -> bool: ...

    @abstractmethod
    async def get_account_state(self) -> dict[str, str | int | float | bool | None]: ...

    @abstractmethod
    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]: ...

    @abstractmethod
    async def get_torrent_details(self, torrent_id: str) -> TorrentDetails: ...

    @abstractmethod
    async def fetch_torrent(self, torrent_id: str) -> bytes: ...


class DownloaderAdapter(ABC):
    @abstractmethod
    def manifest(self) -> AdapterManifest: ...

    @abstractmethod
    async def probe(self) -> ProbeResult: ...

    @abstractmethod
    async def add_torrent(self, torrent: bytes) -> str: ...

    @abstractmethod
    async def get_torrent(self, torrent_id: str) -> dict[str, object]: ...

    @abstractmethod
    async def get_files(self, torrent_id: str) -> list[dict[str, object]]: ...

    @abstractmethod
    async def get_status(self, torrent_id: str) -> dict[str, object]: ...


class ReadOnlyDownloaderAdapter(ABC):
    @abstractmethod
    def manifest(self) -> AdapterManifest: ...

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def get_version(self) -> str: ...

    @abstractmethod
    async def get_web_api_version(self) -> str: ...

    @abstractmethod
    async def list_torrents(self) -> list[QbTorrent]: ...

    async def find_torrents_by_hashes(self, hashes: Collection[str]) -> list[QbTorrent]:
        normalized: set[str] = set()
        for value in hashes:
            info_hash = value.casefold()
            normalized.add(info_hash)
            if len(info_hash) == 64:
                normalized.add(info_hash[:40])
        return [
            torrent
            for torrent in await self.list_torrents()
            if normalized.intersection(torrent.identity_hashes)
        ]

    async def list_recent_torrents(self, limit: int) -> list[QbTorrent]:
        if limit <= 0:
            return []
        torrents = await self.list_torrents()
        return sorted(torrents, key=lambda torrent: torrent.added_on, reverse=True)[:limit]

    async def has_active_seeding(self) -> bool:
        return any(
            torrent.progress == 1
            and (
                torrent.upspeed > 0
                or torrent.state.casefold() in {"uploading", "forcedup"}
            )
            for torrent in await self.list_torrents()
        )

    @abstractmethod
    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]: ...

    @abstractmethod
    async def get_categories(self) -> dict[str, QbCategory]: ...
