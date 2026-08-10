from app.adapters.base import DownloaderAdapter
from app.errors import AppError
from app.schemas.adapters import AdapterManifest, ProbeResult


class DisabledDownloaderAdapter(DownloaderAdapter):
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="downloader-disabled",
            name="下载器接口",
            adapter_type="downloader",
            version="1.0",
            enabled=False,
            mode="INTERFACE_ONLY",
            description="本阶段不连接真实 qBittorrent，所有写操作均禁用",
            capabilities={
                "probe": True,
                "add_torrent": False,
                "get_torrent": False,
                "get_files": False,
                "get_status": False,
            },
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=False, error_code="PHASE_NOT_ENABLED", message="本阶段未启用")

    @staticmethod
    def _disabled() -> AppError:
        return AppError("PHASE_NOT_ENABLED", "当前阶段禁止连接或操作下载器", status_code=403)

    async def add_torrent(self, torrent: bytes) -> str:
        del torrent
        raise self._disabled()

    async def get_torrent(self, torrent_id: str) -> dict[str, object]:
        del torrent_id
        raise self._disabled()

    async def get_files(self, torrent_id: str) -> list[dict[str, object]]:
        del torrent_id
        raise self._disabled()

    async def get_status(self, torrent_id: str) -> dict[str, object]:
        del torrent_id
        raise self._disabled()
