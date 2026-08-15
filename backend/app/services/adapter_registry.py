from app.adapters.downloaders import DisabledDownloaderAdapter
from app.adapters.pt_sites.catalog import PtSiteCatalog, build_pt_site_catalog
from app.core.config import Settings
from app.schemas.adapters import AdapterManifest, PtSearchMode


def adapter_manifests(
    settings: Settings,
    pt_site_catalog: PtSiteCatalog | None = None,
) -> list[AdapterManifest]:
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
    catalog = pt_site_catalog or build_pt_site_catalog(settings)
    pt_sites = [
        AdapterManifest(
            id=entry.site_id,
            name=entry.display_name,
            adapter_type="pt_site",
            version="1.0",
            enabled=entry.available_for_search,
            mode=entry.mode,
            description=entry.description,
            capabilities={
                "tmdb_search": PtSearchMode.TMDB_ID in entry.search_modes,
                "imdb_search": PtSearchMode.IMDB_ID in entry.search_modes,
                "text_search": PtSearchMode.TEXT in entry.search_modes,
                "promotion_parsing": entry.promotion_metadata,
                "hit_and_run_parsing": entry.hit_and_run_metadata,
                "manual_only": entry.manual_only,
                "fetch_torrent_enabled": entry.torrent_fetch_enabled,
                "write_operations": False,
            },
        )
        for entry in catalog.list_public()
    ]
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
        *pt_sites,
        qbittorrent,
        DisabledDownloaderAdapter().manifest(),
    ]
