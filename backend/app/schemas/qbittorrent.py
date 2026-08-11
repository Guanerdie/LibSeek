from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_INT64 = 9_223_372_036_854_775_807
MAX_TIMESTAMP = 253_402_300_799
InfoHash = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$")]
V1InfoHash = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{40}$")]
V2InfoHash = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class QbTorrent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hash: InfoHash
    infohash_v1: V1InfoHash | None = None
    infohash_v2: V2InfoHash | None = None
    name: str = Field(min_length=1, max_length=1000)
    size: int = Field(ge=0, le=MAX_INT64)
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    ratio: float = Field(ge=-1, allow_inf_nan=False)
    state: str = Field(min_length=1, max_length=100)
    added_on: int = Field(default=0, ge=-1, le=MAX_TIMESTAMP)
    completion_on: int = Field(default=0, ge=-1, le=MAX_TIMESTAMP)
    seeding_time: int = Field(default=0, ge=0, le=MAX_INT64)
    downloaded: int = Field(default=0, ge=0, le=MAX_INT64)
    uploaded: int = Field(default=0, ge=0, le=MAX_INT64)
    dlspeed: int = Field(default=0, ge=0, le=MAX_INT64)
    upspeed: int = Field(default=0, ge=0, le=MAX_INT64)
    category: str = Field(default="", max_length=300)
    tags: str = Field(default="", max_length=2000)
    save_path: str = Field(default="", max_length=4096)

    @field_validator("infohash_v1", "infohash_v2", mode="before")
    @classmethod
    def normalize_optional_hash(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("hash", "infohash_v1", "infohash_v2")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else None

    @property
    def identity_hashes(self) -> frozenset[str]:
        hashes = {value for value in (self.hash, self.infohash_v1, self.infohash_v2) if value}
        if self.infohash_v2 is not None:
            hashes.add(self.infohash_v2[:40])
        return frozenset(hashes)


class QbTorrentFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0, le=2_147_483_647)
    name: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0, le=MAX_INT64)
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    priority: int = Field(ge=0, le=7)


class QbCategory(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(default="", max_length=300)
    save_path: str = Field(default="", alias="savePath", max_length=4096)


class QbStatus(BaseModel):
    connected: bool
    application_version: str
    web_api_version: str
    torrent_count: int = Field(ge=0)
    category_count: int = Field(ge=0)
    active_seeding_count: int = Field(ge=0)


class QbTorrentList(BaseModel):
    items: list[QbTorrent]
    total: int = Field(ge=0)
