from app.adapters.downloaders import DisabledDownloaderAdapter
from app.adapters.metadata import MockTmdbProvider
from app.adapters.pt_sites import AvistaZMockAdapter
from app.core.config import Settings
from app.schemas.adapters import AdapterManifest


def adapter_manifests(settings: Settings) -> list[AdapterManifest]:
    nextfind = AdapterManifest(
        id="nextfind",
        name="NextFind",
        adapter_type="media_source",
        version="1.0",
        enabled=settings.nextfind_configured,
        mode="READ_ONLY",
        description="只读取未入库影视和本地媒体信息",
        capabilities={
            "list_missing_media": True,
            "library_details": True,
            "write_operations": False,
        },
    )
    tmdb = AdapterManifest(
        id="tmdb",
        name="TMDB",
        adapter_type="metadata",
        version="1.0",
        enabled=settings.enable_tmdb_live and settings.tmdb_configured,
        mode="LIVE_READ_ONLY" if settings.enable_tmdb_live else "DISABLED_BY_DEFAULT",
        description="真实只读 TMDB Provider；Token 仅从运行时 Secret 读取",
        capabilities={
            "get_by_tmdb_id": True,
            "search": True,
            "external_ids": True,
            "tv_episode_matrix": True,
            "write_operations": False,
        },
    )
    avistaz = AdapterManifest(
        id="avistaz",
        name="AvistaZ",
        adapter_type="pt_site",
        version="1.0",
        enabled=settings.enable_avistaz_live_search and settings.avistaz_configured,
        mode=(
            "LIVE_READ_ONLY_SEARCH"
            if settings.enable_avistaz_live_search
            else "DISABLED_BY_DEFAULT"
        ),
        description="真实只读候选搜索；认证 Token 只在进程内存，下载端点硬禁用",
        capabilities={
            "tmdb_search": True,
            "imdb_search": True,
            "text_search": True,
            "promotion_parsing": True,
            "hit_and_run_parsing": True,
            "fetch_torrent_enabled": False,
            "write_operations": False,
        },
    )
    qbittorrent = AdapterManifest(
        id="qbittorrent-read-only",
        name="qBittorrent",
        adapter_type="downloader",
        version="1.0",
        enabled=settings.enable_qb_read_only and settings.qb_configured,
        mode="LIVE_READ_ONLY" if settings.enable_qb_read_only else "DISABLED_BY_DEFAULT",
        description="SID 会话只读状态、版本、种子、文件和分类查询",
        capabilities={
            "login": True,
            "version": True,
            "web_api_version": True,
            "list_torrents": True,
            "list_files": True,
            "list_categories": True,
            "write_operations": False,
        },
    )
    return [
        nextfind,
        tmdb,
        avistaz,
        qbittorrent,
        MockTmdbProvider().manifest(),
        AvistaZMockAdapter().manifest(),
        DisabledDownloaderAdapter().manifest(),
    ]
