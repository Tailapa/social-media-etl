from app.models.pydantic.enums import PlatformName
from app.normalization import instagram, twitter, youtube
from app.normalization.common import (
    as_int,
    dedupe_by_key,
    first_present,
    get_or_register,
    merge_prefer_non_null,
)

#: Registry used by the ingestion pipeline to reach a platform's
#: `extract_engagement` (and other normalizer functions) generically, so
#: `app/ingestion/pipeline.py` never branches on platform by name.
NORMALIZERS = {
    PlatformName.INSTAGRAM: instagram,
    PlatformName.TWITTER: twitter,
    PlatformName.YOUTUBE: youtube,
}

__all__ = [
    "instagram",
    "twitter",
    "youtube",
    "NORMALIZERS",
    "dedupe_by_key",
    "get_or_register",
    "merge_prefer_non_null",
    "first_present",
    "as_int",
]
