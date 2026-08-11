import pytest

from app.core.security import sanitize_details, validate_external_url
from app.errors import AppError


def test_sanitize_details_redacts_nested_secrets() -> None:
    value = sanitize_details(
        {
            "password": "secret",
            "nested": {"Authorization": "Bearer abc"},
            "message": "https://x.invalid/a?passkey=abc&safe=1",
        }
    )
    assert value["password"] == "[REDACTED]"
    assert value["nested"]["Authorization"] == "[REDACTED]"
    assert "abc" not in value["message"]


def test_sanitize_details_redacts_urls_and_single_segment_paths() -> None:
    value = sanitize_details(
        {
            "message": (
                r"qB https://qb.internal.test failed at /data, D:/data and \\server\share"
            )
        }
    )["message"]
    assert "qb.internal.test" not in value
    assert "/data" not in value
    assert "D:/data" not in value
    assert "server" not in value


def test_external_url_requires_https_allowlist() -> None:
    assert (
        validate_external_url("https://nextfind.example/api", ("nextfind.example",))
        == "https://nextfind.example/api"
    )
    with pytest.raises(AppError):
        validate_external_url("https://evil.invalid", ("nextfind.example",))
    with pytest.raises(AppError):
        validate_external_url("http://nextfind.example", ("nextfind.example",))
