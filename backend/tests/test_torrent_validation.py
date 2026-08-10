from __future__ import annotations

import hashlib

import bencodepy
import pytest

from app.errors import AppError
from app.services.torrent_validation import validate_torrent


def v1_torrent(*, name: bytes = b"Example.mkv", length: int = 20_000) -> bytes:
    info = {
        b"length": length,
        b"name": name,
        b"piece length": 16_384,
        b"pieces": b"p" * (20 * 2),
        b"private": 1,
    }
    return bencodepy.encode({b"announce": b"https://secret.invalid/a", b"info": info})


def test_validates_v1_and_returns_only_safe_summary() -> None:
    payload = v1_torrent()
    decoded = bencodepy.decode(payload)
    expected = hashlib.sha1(bencodepy.encode(decoded[b"info"])).hexdigest()

    result = validate_torrent(payload, expected_info_hash=expected.upper())

    assert result.name == "Example.mkv"
    assert result.total_size_bytes == 20_000
    assert result.file_count == 1
    assert result.info_hash_v1 == expected
    assert result.info_hash_v2 is None
    assert result.private is True
    assert "secret.invalid" not in repr(result)


def test_rejects_hash_mismatch_and_oversized_torrent() -> None:
    payload = v1_torrent()
    with pytest.raises(AppError) as mismatch:
        validate_torrent(payload, expected_info_hash="0" * 40)
    assert mismatch.value.error_code == "TORRENT_INFO_HASH_MISMATCH"

    with pytest.raises(AppError) as oversized:
        validate_torrent(payload, max_torrent_bytes=8)
    assert oversized.value.error_code == "TORRENT_FILE_TOO_LARGE"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-bencode",
        bencodepy.encode({b"announce": b"https://example.invalid"}),
        v1_torrent(name=b"../unsafe.mkv"),
    ],
)
def test_rejects_empty_malformed_or_unsafe_metadata(payload: bytes) -> None:
    with pytest.raises(AppError) as caught:
        validate_torrent(payload)
    assert caught.value.error_code == "TORRENT_INVALID"


def test_validates_v2_file_tree_and_sha256_hash() -> None:
    info = {
        b"file tree": {
            b"Season 01": {
                b"Episode 01.mkv": {
                    b"": {b"length": 10, b"pieces root": b"a" * 32}
                },
                b"Episode 02.mkv": {
                    b"": {b"length": 20, b"pieces root": b"b" * 32}
                },
            }
        },
        b"meta version": 2,
        b"name": b"Example Show",
        b"piece length": 16_384,
    }
    payload = bencodepy.encode({b"info": info})
    expected = hashlib.sha256(bencodepy.encode(bencodepy.decode(payload)[b"info"])).hexdigest()

    result = validate_torrent(payload, expected_info_hash=expected)

    assert result.info_hash_v1 is None
    assert result.info_hash_v2 == expected
    assert expected[:40] in result.identity_hashes
    assert result.matches_hash(expected[:40])
    assert result.total_size_bytes == 30
    assert result.file_count == 2
    assert result.meta_version == 2


def test_rejects_inconsistent_v1_piece_count() -> None:
    payload = bencodepy.encode(
        {
            b"info": {
                b"length": 100_000,
                b"name": b"bad.mkv",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            }
        }
    )
    with pytest.raises(AppError) as caught:
        validate_torrent(payload)
    assert caught.value.error_code == "TORRENT_INVALID"


def test_rejects_ambiguous_v1_shape_before_files_can_bypass_validation() -> None:
    payload = bencodepy.encode(
        {
            b"info": {
                b"files": [
                    {
                        b"length": 1,
                        b"path": [b"..", b"escaped.mkv"],
                    }
                ],
                b"length": 1,
                b"name": b"ambiguous",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            }
        }
    )

    with pytest.raises(AppError) as caught:
        validate_torrent(payload)

    assert caught.value.error_code == "TORRENT_INVALID"


def test_rejects_symlinks_and_huge_integer_values_as_invalid_torrents() -> None:
    symlink = bencodepy.encode(
        {
            b"info": {
                b"files": [
                    {
                        b"attr": b"l",
                        b"length": 1,
                        b"path": [b"link.mkv"],
                        b"symlink path": [b"..", b"outside.mkv"],
                    }
                ],
                b"name": b"unsafe",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            }
        }
    )
    huge_length = bencodepy.encode(
        {
            b"info": {
                b"length": 10**400,
                b"name": b"huge.mkv",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            }
        }
    )
    decoder_limit = b"i" + (b"9" * 5000) + b"e"

    for payload in (symlink, huge_length, decoder_limit):
        with pytest.raises(AppError) as caught:
            validate_torrent(payload)
        assert caught.value.error_code == "TORRENT_INVALID"
