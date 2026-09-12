#!/usr/bin/env python3
"""Download XMLTV data and normalise programme times to Australia/Sydney."""

from __future__ import annotations

import gzip
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE_URL = "https://live.fanmingming.cn/e.xml"
OUTPUT_XML = Path("docs/epg.xml")
OUTPUT_GZIP = Path("docs/epg.xml.gz")

SOURCE_DEFAULT_TZ = ZoneInfo("Asia/Shanghai")
TARGET_TZ = ZoneInfo("Australia/Sydney")

# XMLTV commonly uses 12 or 14 digits followed by an optional offset such as
# +0800.  A missing offset is legal in some feeds; this project treats it as
# China Standard Time because that is the source feed's documented context.
TIMESTAMP_RE = re.compile(
    r"^(?P<stamp>\d{12}|\d{14})(?:\s*(?P<offset>Z|[+-]\d{2}:?\d{2}))?$"
)
PROGRAMME_TAG_RE = re.compile(r"<programme\b[^>]*>", re.IGNORECASE)
TIME_ATTRIBUTE_RE = re.compile(
    r"(?P<name>start|stop)(?P<separator>\s*=\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\"']*)(?P=quote)",
    re.IGNORECASE,
)


def parse_xmltv_timestamp(value: str) -> datetime:
    """Parse one XMLTV timestamp, using Shanghai when no offset is present."""

    match = TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported XMLTV timestamp: {value!r}")

    stamp = match.group("stamp")
    fmt = "%Y%m%d%H%M" if len(stamp) == 12 else "%Y%m%d%H%M%S"
    naive = datetime.strptime(stamp, fmt)
    offset = match.group("offset")

    if not offset:
        source_tz = SOURCE_DEFAULT_TZ
    elif offset == "Z":
        source_tz = timezone.utc
    else:
        compact = offset.replace(":", "")
        hours = int(compact[1:3])
        minutes = int(compact[3:5])
        if hours > 23 or minutes > 59:
            raise ValueError(f"Invalid XMLTV timezone offset: {offset!r}")
        delta = timedelta(hours=hours, minutes=minutes)
        source_tz = timezone(delta if compact[0] == "+" else -delta)

    return naive.replace(tzinfo=source_tz)


def format_sydney_timestamp(value: str) -> str:
    """Convert one XMLTV timestamp to a Sydney-local timestamp with offset."""

    cleaned = value.strip()
    match = TIMESTAMP_RE.fullmatch(cleaned)
    if not match:
        raise ValueError(f"Unsupported XMLTV timestamp: {value!r}")
    parsed = parse_xmltv_timestamp(cleaned)
    local = parsed.astimezone(TARGET_TZ)
    stamp_format = "%Y%m%d%H%M" if len(match.group("stamp")) == 12 else "%Y%m%d%H%M%S"
    return f"{local.strftime(stamp_format)} {local.strftime('%z')}"


def convert_programme_tag(tag: str) -> tuple[str, int]:
    """Convert start/stop attributes in one programme opening tag."""

    converted = 0

    def replace_attribute(match: re.Match[str]) -> str:
        nonlocal converted
        converted += 1
        return (
            f"{match.group('name')}{match.group('separator')}"
            f"{match.group('quote')}{format_sydney_timestamp(match.group('value'))}"
            f"{match.group('quote')}"
        )

    return TIME_ATTRIBUTE_RE.sub(replace_attribute, tag), converted


def convert_xml(xml: str) -> tuple[str, int, int]:
    """Convert all programme timestamps and return XML, programme and time counts."""

    programmes = 0
    timestamps = 0

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal programmes, timestamps
        programmes += 1
        converted_tag, count = convert_programme_tag(match.group(0))
        timestamps += count
        return converted_tag

    converted = PROGRAMME_TAG_RE.sub(replace_tag, xml)
    if programmes == 0:
        raise ValueError("Source does not contain any <programme> elements")
    if timestamps == 0:
        raise ValueError("Source contains no programme start/stop timestamps")
    return converted, programmes, timestamps


def download_source() -> str:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "epg-sydney/1.0 (+https://github.com/BabyFoxy/epg-sydney)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def write_outputs(xml: str) -> None:
    OUTPUT_XML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_XML.write_text(xml, encoding="utf-8")
    OUTPUT_GZIP.write_bytes(gzip.compress(xml.encode("utf-8"), compresslevel=9, mtime=0))


def main() -> None:
    source = download_source()
    converted, programmes, timestamps = convert_xml(source)
    if not converted.lstrip().startswith("<?xml") or "<tv" not in converted[:1000]:
        raise ValueError("Downloaded content is not recognised as XMLTV")
    write_outputs(converted)
    print(
        f"Generated {OUTPUT_XML} ({OUTPUT_XML.stat().st_size:,} bytes) and "
        f"{OUTPUT_GZIP} ({OUTPUT_GZIP.stat().st_size:,} bytes); "
        f"converted {timestamps:,} timestamps in {programmes:,} programmes."
    )


if __name__ == "__main__":
    main()
