from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import bencodepy  # type: ignore[import-untyped]

from app.errors import AppError

_INFO_HASH = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_MAX_PIECE_LENGTH = 64 * 1024 * 1024
_MAX_TOTAL_SIZE = 16 * 1024**5


@dataclass(frozen=True)
class ValidatedTorrent:
    name: str
    total_size_bytes: int
    file_count: int
    info_hash_v1: str | None
    info_hash_v2: str | None
    private: bool | None
    meta_version: int

    @property
    def identity_hashes(self) -> frozenset[str]:
        hashes = {value for value in (self.info_hash_v1, self.info_hash_v2) if value}
        if self.info_hash_v2 is not None:
            hashes.add(self.info_hash_v2[:40])
        return frozenset(hashes)

    def matches_hash(self, expected: str) -> bool:
        return expected.casefold() in self.identity_hashes


def validate_torrent(
    payload: bytes,
    *,
    expected_info_hash: str | None = None,
    max_torrent_bytes: int = 10 * 1024 * 1024,
    max_files: int = 20_000,
) -> ValidatedTorrent:
    if not payload:
        raise _invalid("种子文件为空")
    if len(payload) > max(1, max_torrent_bytes):
        raise AppError(
            "TORRENT_FILE_TOO_LARGE",
            "种子文件超过安全大小限制",
            status_code=502,
        )
    try:
        root = bencodepy.decode(payload)
        canonical = bencodepy.encode(root)
    except (
        bencodepy.DecodingError,
        bencodepy.EncodingError,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        raise _invalid("种子文件不是有效的 bencode 数据") from exc
    if canonical != payload:
        raise _invalid("种子文件使用了非规范 bencode 编码")
    if not isinstance(root, Mapping):
        raise _invalid("种子文件顶层结构无效")
    info = root.get(b"info")
    if not isinstance(info, Mapping):
        raise _invalid("种子文件缺少有效的 info 字典")

    encoded_info = bencodepy.encode(info)
    meta_version = _positive_integer(info.get(b"meta version"), default=1)
    if meta_version not in {1, 2}:
        raise _invalid("不支持的种子元数据版本")

    name_bytes = info.get(b"name.utf-8", info.get(b"name"))
    name = _decode_component(name_bytes, field="name")
    private = _private_flag(info.get(b"private"))
    v1_shape = b"length" in info or b"files" in info or b"pieces" in info
    v2_shape = b"file tree" in info
    if not v1_shape and not v2_shape:
        raise _invalid("种子文件没有可识别的文件结构")

    v1_size: int | None = None
    v1_count: int | None = None
    if v1_shape:
        v1_size, v1_count = _validate_v1_files(info, max_files=max_files)
    v2_size: int | None = None
    v2_count: int | None = None
    if v2_shape:
        v2_size, v2_count = _validate_v2_files(info, max_files=max_files)
    if v1_size is not None and v2_size is not None and v1_size != v2_size:
        raise _invalid("混合种子的 v1/v2 文件大小不一致")
    if v1_count is not None and v2_count is not None and v1_count != v2_count:
        raise _invalid("混合种子的 v1/v2 文件数量不一致")

    total_size = v1_size if v1_size is not None else v2_size
    file_count = v1_count if v1_count is not None else v2_count
    if total_size is None or file_count is None or total_size <= 0 or file_count <= 0:
        raise _invalid("种子文件的内容大小无效")
    if total_size > _MAX_TOTAL_SIZE:
        raise _invalid("种子声明的内容大小超过安全限制")

    info_hash_v1 = hashlib.sha1(encoded_info).hexdigest() if v1_shape else None
    info_hash_v2 = hashlib.sha256(encoded_info).hexdigest() if meta_version == 2 else None
    result = ValidatedTorrent(
        name=name,
        total_size_bytes=total_size,
        file_count=file_count,
        info_hash_v1=info_hash_v1,
        info_hash_v2=info_hash_v2,
        private=private,
        meta_version=meta_version,
    )
    if expected_info_hash is not None:
        if not _INFO_HASH.fullmatch(expected_info_hash) or not result.matches_hash(
            expected_info_hash
        ):
            raise AppError(
                "TORRENT_INFO_HASH_MISMATCH",
                "种子文件与已审批候选的 info hash 不一致",
                status_code=409,
            )
    return result


def _validate_v1_files(info: Mapping[bytes, Any], *, max_files: int) -> tuple[int, int]:
    piece_length = _positive_integer(info.get(b"piece length"))
    if piece_length > _MAX_PIECE_LENGTH:
        raise _invalid("种子分片大小超过安全限制")
    pieces = info.get(b"pieces")
    if not isinstance(pieces, bytes) or not pieces or len(pieces) % 20:
        raise _invalid("v1 种子的 pieces 字段无效")

    has_length = b"length" in info
    has_files = b"files" in info
    if has_length == has_files:
        raise _invalid("v1 种子必须且只能声明单文件或多文件结构")

    if has_length:
        _validate_file_attributes(info)
        total_size = _content_length(info.get(b"length"))
        file_count = 1
    else:
        files = info.get(b"files")
        if not isinstance(files, list) or not files or len(files) > max_files:
            raise _invalid("v1 种子的文件列表无效或过大")
        total_size = 0
        seen_paths: set[tuple[str, ...]] = set()
        for file_item in files:
            if not isinstance(file_item, Mapping):
                raise _invalid("v1 种子的文件条目无效")
            _validate_file_attributes(file_item)
            total_size += _content_length(file_item.get(b"length"))
            if total_size > _MAX_TOTAL_SIZE:
                raise _invalid("种子声明的内容大小超过安全限制")
            path = file_item.get(b"path.utf-8", file_item.get(b"path"))
            if not isinstance(path, list) or not path:
                raise _invalid("v1 种子的文件路径无效")
            decoded_path = tuple(
                _decode_component(component, field="path") for component in path
            )
            if decoded_path in seen_paths:
                raise _invalid("v1 种子的文件路径重复")
            seen_paths.add(decoded_path)
        file_count = len(files)

    expected_pieces = (total_size + piece_length - 1) // piece_length
    if len(pieces) != expected_pieces * 20:
        raise _invalid("v1 种子的分片数量与内容大小不一致")
    return total_size, file_count


def _validate_v2_files(info: Mapping[bytes, Any], *, max_files: int) -> tuple[int, int]:
    if _positive_integer(info.get(b"meta version")) != 2:
        raise _invalid("v2 文件树缺少 meta version 2")
    piece_length = _positive_integer(info.get(b"piece length"))
    if piece_length > _MAX_PIECE_LENGTH or piece_length & (piece_length - 1):
        raise _invalid("v2 种子的分片大小无效")
    tree = info.get(b"file tree")
    if not isinstance(tree, Mapping) or not tree:
        raise _invalid("v2 种子的文件树无效")

    total_size = 0
    file_count = 0

    def walk(node: Mapping[bytes, Any], depth: int) -> None:
        nonlocal total_size, file_count
        if depth > 100:
            raise _invalid("v2 种子的文件树层级过深")
        if b"" in node and len(node) != 1:
            raise _invalid("v2 种子的文件节点不能同时作为目录")
        for raw_name, value in node.items():
            if raw_name == b"":
                if not isinstance(value, Mapping):
                    raise _invalid("v2 种子的文件属性无效")
                _validate_file_attributes(value)
                length = _content_length(value.get(b"length"))
                attributes = value.get(b"attr", b"")
                pieces_root = value.get(b"pieces root")
                is_padding = isinstance(attributes, bytes) and b"p" in attributes
                if not is_padding and (
                    not isinstance(pieces_root, bytes) or len(pieces_root) != 32
                ):
                    raise _invalid("v2 种子的文件缺少有效 pieces root")
                total_size += length
                if total_size > _MAX_TOTAL_SIZE:
                    raise _invalid("种子声明的内容大小超过安全限制")
                file_count += 1
                if file_count > max_files:
                    raise _invalid("v2 种子的文件数量超过安全限制")
                continue
            _decode_component(raw_name, field="path")
            if not isinstance(value, Mapping):
                raise _invalid("v2 种子的文件树节点无效")
            walk(value, depth + 1)

    walk(tree, 0)
    return total_size, file_count


def _content_length(value: Any) -> int:
    length = _positive_integer(value)
    if length > _MAX_TOTAL_SIZE:
        raise _invalid("种子声明的内容大小超过安全限制")
    return length


def _validate_file_attributes(value: Mapping[bytes, Any]) -> None:
    attributes = value.get(b"attr")
    if attributes is not None and not isinstance(attributes, bytes):
        raise _invalid("种子文件的 attr 字段无效")
    if b"symlink path" in value or (isinstance(attributes, bytes) and b"l" in attributes):
        raise _invalid("种子文件包含不允许的符号链接")


def _positive_integer(value: Any, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _invalid("种子文件包含无效的正整数字段")
    return int(value)


def _private_flag(value: Any) -> bool | None:
    if value is None:
        return None
    if value not in {0, 1} or isinstance(value, bool):
        raise _invalid("种子文件的 private 字段无效")
    return bool(value)


def _decode_component(value: Any, *, field: str) -> str:
    if not isinstance(value, bytes) or not value:
        raise _invalid(f"种子文件的 {field} 字段无效")
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid(f"种子文件的 {field} 字段不是 UTF-8") from exc
    if decoded in {".", ".."} or any(marker in decoded for marker in ("/", "\\", "\x00")):
        raise _invalid(f"种子文件的 {field} 字段包含不安全路径")
    return decoded


def _invalid(message: str) -> AppError:
    return AppError("TORRENT_INVALID", message, status_code=502)
