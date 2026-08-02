from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def _serialize_utc(dt: datetime) -> str:
    """Serialize a datetime as an ISO-8601 string with an explicit UTC offset.

    Timestamps are stored naive in the DB via ``datetime.utcnow()`` (always UTC).
    A naive datetime serializes to JSON with no timezone suffix, which JS
    ``new Date(...)`` then parses as *local* time — shifting every displayed
    timestamp by the browser's UTC offset. Attaching ``+00:00`` here makes the
    wire format unambiguous so the frontend parses it as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Use for every datetime field on an API response schema. The DB stores naive
# UTC datetimes; this guarantees the JSON always carries the UTC offset.
UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


def mask_secret(value: str | None) -> str:
    """Render a stored secret for display: the last four characters, the rest as asterisks.

    Four characters is enough for a human to recognise which key they saved without the
    value being reusable. Anything four characters or shorter masks *entirely* — a short
    value would otherwise be echoed back in full.

    Shared by ``OpenAIProviderOut.api_key_masked`` and ``SecretOut.masked`` so the two
    surfaces cannot drift into different notions of how much of a key is safe to show.
    """
    key = value or ""
    if len(key) > 4:
        return "*" * (len(key) - 4) + key[-4:]
    return "*" * len(key)
