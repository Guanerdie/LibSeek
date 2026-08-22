from enum import StrEnum


class MediaType(StrEnum):
    MOVIE = "movie"
    TV = "tv"


class IdentityConfidence(StrEnum):
    HIGH = "HIGH"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


class MetadataStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    UNRESOLVED = "UNRESOLVED"


class AuthRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
