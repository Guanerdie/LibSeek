from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.core.config import Settings
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import PreflightStatus
from app.schemas.approvals import ApprovalCandidateSnapshot, PreflightCheck, PreflightResult
from app.schemas.qbittorrent import QbCategory, QbTorrent

PREFLIGHT_CHECK_CODES = (
    "QB_CONNECTION",
    "QB_APP_VERSION",
    "QB_WEB_API_VERSION",
    "AVISTAZ_QB_VERSION_RULE",
    "TARGET_CATEGORY",
    "DUPLICATE_INFO_HASH",
    "POSSIBLE_DUPLICATE_RELEASE",
    "SAVE_PATH_ALLOWED",
    "SIZE_LIMIT",
    "ACTIVE_SEEDING",
    "CANDIDATE_SEEDERS",
    "HNR_KNOWN",
)


def preflight_policy_fingerprint(settings: Settings) -> str:
    normalized_base_url = _normalized_qb_base_url_for_policy(settings)
    allowed_hosts = sorted(
        {host.strip().casefold() for host in settings.qb_allowed_hosts if host.strip()}
    )
    policy = {
        "version": 2,
        "downloader": "qbittorrent",
        "base_url": normalized_base_url,
        "target_instance_ref": settings.qb_target_instance_ref,
        "allowed_hosts": allowed_hosts,
        "allow_insecure_http": settings.qb_allow_insecure_http,
        "target_category": settings.qb_target_category,
        "target_save_path": settings.qb_target_save_path,
        "allowed_save_paths": sorted(settings.qb_allowed_save_paths),
        "save_path_ref": settings.qb_save_path_ref,
        "plan_tags": list(settings.qb_plan_tags),
        "max_candidate_size_bytes": settings.max_candidate_size_bytes,
        "avistaz_forbidden_qb_versions": sorted(settings.avistaz_forbidden_qb_versions),
        "preflight_max_age_seconds": settings.approval_preflight_max_age_seconds,
    }
    canonical = json.dumps(
        policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def require_preflight_policy_current(
    expected_fingerprint: str,
    settings: Settings,
) -> None:
    current_fingerprint = preflight_policy_fingerprint(settings)
    if not hmac.compare_digest(expected_fingerprint, current_fingerprint):
        raise AppError(
            "PREFLIGHT_CONFIG_CHANGED",
            "qBittorrent 预检目标配置已变化，请重新预检并批准",
            status_code=409,
        )


def _normalized_qb_base_url_for_policy(settings: Settings) -> str:
    try:
        configured_url = settings.qb_base_url_value()
    except OSError as exc:
        raise AppError(
            "PREFLIGHT_TARGET_CONFIG_UNAVAILABLE",
            "qBittorrent 预检目标配置无法读取",
            status_code=409,
        ) from exc
    if not configured_url:
        raise AppError(
            "PREFLIGHT_TARGET_CONFIG_UNAVAILABLE",
            "qBittorrent 预检目标尚未配置",
            status_code=409,
        )

    raw_url = configured_url.strip()
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise AppError(
            "PREFLIGHT_TARGET_CONFIG_INVALID",
            "qBittorrent 预检目标地址不符合安全配置",
            status_code=409,
        ) from exc

    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    allowed_hosts = {
        allowed.strip().casefold()
        for allowed in settings.qb_allowed_hosts
        if allowed.strip()
    }
    allowed_schemes = {"https", "http"} if settings.qb_allow_insecure_http else {"https"}
    if (
        configured_url != raw_url
        or any(ord(character) < 33 or ord(character) == 127 for character in raw_url)
        or scheme not in allowed_schemes
        or not host
        or host not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(
            "PREFLIGHT_TARGET_CONFIG_INVALID",
            "qBittorrent 预检目标地址不符合安全配置",
            status_code=409,
        )

    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    normalized_host = f"[{host}]" if ":" in host else host
    normalized_port = "" if port is None or default_port else f":{port}"
    normalized_path = parsed.path.rstrip("/")
    return f"{scheme}://{normalized_host}{normalized_port}{normalized_path}"


async def evaluate_preflight(
    adapter: ReadOnlyDownloaderAdapter,
    snapshot: ApprovalCandidateSnapshot,
    settings: Settings,
) -> PreflightResult:
    policy_fingerprint = preflight_policy_fingerprint(settings)
    checks: list[PreflightCheck] = []
    application_version: str | None = None
    torrents: list[QbTorrent] | None = None
    categories: dict[str, QbCategory] | None = None

    try:
        await adapter.authenticate()
    except AppError as exc:
        checks.append(
            _check(
                "QB_CONNECTION",
                PreflightStatus.BLOCKED,
                "qBittorrent 只读连接失败",
                error_code=exc.error_code,
            )
        )
    else:
        checks.append(_check("QB_CONNECTION", PreflightStatus.PASS, "qBittorrent 只读连接正常"))
        try:
            application_version = await adapter.get_version()
            checks.append(
                _check(
                    "QB_APP_VERSION",
                    PreflightStatus.PASS,
                    "已读取 qBittorrent 应用版本",
                    version=application_version,
                )
            )
        except AppError as exc:
            checks.append(
                _check(
                    "QB_APP_VERSION",
                    PreflightStatus.UNKNOWN,
                    "无法确认 qBittorrent 应用版本",
                    error_code=exc.error_code,
                )
            )
        try:
            web_api_version = await adapter.get_web_api_version()
            checks.append(
                _check(
                    "QB_WEB_API_VERSION",
                    PreflightStatus.PASS,
                    "已读取 qBittorrent Web API 版本",
                    version=web_api_version,
                )
            )
        except AppError as exc:
            checks.append(
                _check(
                    "QB_WEB_API_VERSION",
                    PreflightStatus.UNKNOWN,
                    "无法确认 qBittorrent Web API 版本",
                    error_code=exc.error_code,
                )
            )
        try:
            torrents = await adapter.list_torrents()
        except AppError:
            torrents = None
        try:
            categories = await adapter.get_categories()
        except AppError:
            categories = None

    checks.append(_avistaz_version_check(application_version, settings))
    checks.append(_category_check(categories, settings.qb_target_category))
    checks.append(_info_hash_check(torrents, snapshot.info_hash))
    checks.append(_duplicate_release_check(torrents, snapshot))
    checks.append(_save_path_check(settings))
    checks.append(_size_limit_check(snapshot.size_bytes, settings.max_candidate_size_bytes))
    checks.append(_active_seeding_check(torrents))
    checks.append(_candidate_seeders_check(snapshot.seeders))
    checks.append(_hnr_check(snapshot.hit_and_run))

    statuses = {check.status for check in checks}
    overall = next(
        status
        for status in (
            PreflightStatus.BLOCKED,
            PreflightStatus.UNKNOWN,
            PreflightStatus.WARNING,
            PreflightStatus.PASS,
        )
        if status in statuses
    )
    return PreflightResult(
        overall_status=overall,
        checks=checks,
        checked_at=utc_now(),
        policy_fingerprint=policy_fingerprint,
    )


def _check(
    code: str, status: PreflightStatus, message: str, **details: object
) -> PreflightCheck:
    return PreflightCheck(code=code, status=status, message=message, details=details)


def _avistaz_version_check(version: str | None, settings: Settings) -> PreflightCheck:
    if version is None:
        return _check(
            "AVISTAZ_QB_VERSION_RULE",
            PreflightStatus.UNKNOWN,
            "无法根据 AvistaZ 规则判断 qBittorrent 版本",
        )
    rules = settings.avistaz_forbidden_qb_versions
    if not rules:
        return _check(
            "AVISTAZ_QB_VERSION_RULE",
            PreflightStatus.UNKNOWN,
            "尚未配置 AvistaZ 禁止版本规则",
            version=version,
        )
    normalized = version.removeprefix("v")
    forbidden = any(fnmatch.fnmatchcase(normalized, rule.removeprefix("v")) for rule in rules)
    if forbidden:
        return _check(
            "AVISTAZ_QB_VERSION_RULE",
            PreflightStatus.BLOCKED,
            "当前 qBittorrent 版本命中 AvistaZ 禁止规则",
            version=version,
        )
    return _check(
        "AVISTAZ_QB_VERSION_RULE",
        PreflightStatus.PASS,
        "当前 qBittorrent 版本未命中 AvistaZ 禁止规则",
        version=version,
    )


def _category_check(
    categories: dict[str, QbCategory] | None, target_category: str | None
) -> PreflightCheck:
    if not target_category:
        return _check("TARGET_CATEGORY", PreflightStatus.UNKNOWN, "尚未配置目标分类")
    if categories is None:
        return _check("TARGET_CATEGORY", PreflightStatus.UNKNOWN, "无法读取 qBittorrent 分类")
    if target_category not in categories:
        return _check(
            "TARGET_CATEGORY",
            PreflightStatus.BLOCKED,
            "qBittorrent 中不存在目标分类",
            category=target_category,
        )
    return _check(
        "TARGET_CATEGORY",
        PreflightStatus.PASS,
        "qBittorrent 目标分类存在",
        category=target_category,
    )


def _info_hash_check(torrents: list[QbTorrent] | None, info_hash: str | None) -> PreflightCheck:
    if not info_hash:
        return _check("DUPLICATE_INFO_HASH", PreflightStatus.UNKNOWN, "候选没有可校验的 info_hash")
    if torrents is None:
        return _check("DUPLICATE_INFO_HASH", PreflightStatus.UNKNOWN, "无法读取 qBittorrent 任务")
    normalized_hash = info_hash.casefold()
    duplicate = any(normalized_hash in item.identity_hashes for item in torrents)
    if duplicate:
        return _check(
            "DUPLICATE_INFO_HASH",
            PreflightStatus.BLOCKED,
            "qBittorrent 已存在相同 info_hash",
        )
    return _check("DUPLICATE_INFO_HASH", PreflightStatus.PASS, "未发现相同 info_hash")


def _duplicate_release_check(
    torrents: list[QbTorrent] | None, snapshot: ApprovalCandidateSnapshot
) -> PreflightCheck:
    if snapshot.size_bytes is None:
        return _check(
            "POSSIBLE_DUPLICATE_RELEASE",
            PreflightStatus.UNKNOWN,
            "候选大小未知，无法判断发布名和大小重复",
        )
    if torrents is None:
        return _check(
            "POSSIBLE_DUPLICATE_RELEASE",
            PreflightStatus.UNKNOWN,
            "无法读取 qBittorrent 任务以判断疑似重复",
        )
    duplicate = any(
        item.name.casefold().strip() == snapshot.release_title.casefold().strip()
        and item.size == snapshot.size_bytes
        for item in torrents
    )
    if duplicate:
        return _check(
            "POSSIBLE_DUPLICATE_RELEASE",
            PreflightStatus.WARNING,
            "存在相同发布名和大小的疑似重复任务",
        )
    return _check(
        "POSSIBLE_DUPLICATE_RELEASE",
        PreflightStatus.PASS,
        "未发现相同发布名和大小的任务",
    )


def _save_path_check(settings: Settings) -> PreflightCheck:
    target = settings.qb_target_save_path
    roots = settings.qb_allowed_save_paths
    if not target or not roots:
        return _check("SAVE_PATH_ALLOWED", PreflightStatus.UNKNOWN, "目标保存路径或允许路径未配置")
    if not is_allowed_save_path(target, roots):
        return _check(
            "SAVE_PATH_ALLOWED",
            PreflightStatus.BLOCKED,
            "目标保存路径不属于允许路径",
            save_path_ref=settings.qb_save_path_ref,
        )
    return _check(
        "SAVE_PATH_ALLOWED",
        PreflightStatus.PASS,
        "目标保存路径属于允许路径",
        save_path_ref=settings.qb_save_path_ref,
    )


def is_allowed_save_path(target: str, roots: tuple[str, ...]) -> bool:
    path_class: type[PurePath] = (
        PureWindowsPath if re.match(r"^[A-Za-z]:", target) or "\\" in target else PurePosixPath
    )
    target_path = path_class(target)
    if not target_path.is_absolute() or ".." in target_path.parts:
        return False
    for root in roots:
        root_path = path_class(root)
        if not root_path.is_absolute() or ".." in root_path.parts:
            continue
        if target_path == root_path or root_path in target_path.parents:
            return True
    return False


def _size_limit_check(size: int | None, limit: int | None) -> PreflightCheck:
    if size is None:
        return _check("SIZE_LIMIT", PreflightStatus.UNKNOWN, "候选下载大小未知")
    if limit is None:
        return _check("SIZE_LIMIT", PreflightStatus.UNKNOWN, "尚未配置用户下载大小限制")
    if size > limit:
        return _check(
            "SIZE_LIMIT",
            PreflightStatus.BLOCKED,
            "候选大小超过用户限制",
            size_bytes=size,
            limit_bytes=limit,
        )
    return _check(
        "SIZE_LIMIT",
        PreflightStatus.PASS,
        "候选大小未超过用户限制",
        size_bytes=size,
        limit_bytes=limit,
    )


def _active_seeding_check(torrents: list[QbTorrent] | None) -> PreflightCheck:
    if torrents is None:
        return _check("ACTIVE_SEEDING", PreflightStatus.UNKNOWN, "无法读取当前做种任务")
    count = sum(
        item.progress == 1
        and (item.upspeed > 0 or item.state.casefold() in {"uploading", "forcedup"})
        for item in torrents
    )
    if count == 0:
        return _check("ACTIVE_SEEDING", PreflightStatus.WARNING, "当前没有活跃做种任务", count=0)
    return _check("ACTIVE_SEEDING", PreflightStatus.PASS, "当前存在活跃做种任务", count=count)


def _candidate_seeders_check(seeders: int | None) -> PreflightCheck:
    if seeders is None:
        return _check("CANDIDATE_SEEDERS", PreflightStatus.UNKNOWN, "候选做种数未知")
    if seeders == 0:
        return _check("CANDIDATE_SEEDERS", PreflightStatus.BLOCKED, "候选当前没有做种者")
    return _check(
        "CANDIDATE_SEEDERS", PreflightStatus.PASS, "候选当前有做种者", seeders=seeders
    )


def _hnr_check(hit_and_run: bool | None) -> PreflightCheck:
    if hit_and_run is None:
        return _check("HNR_KNOWN", PreflightStatus.UNKNOWN, "候选 H&R 规则未知")
    return _check(
        "HNR_KNOWN",
        PreflightStatus.PASS,
        "候选 H&R 信息已知",
        hit_and_run=hit_and_run,
    )
