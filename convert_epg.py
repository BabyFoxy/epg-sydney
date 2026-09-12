#!/usr/bin/env python3
"""Download XMLTV data and normalise programme times to Australia/Sydney."""

from __future__ import annotations

import gzip
import io
import os
import re
import urllib.request
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape


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
EXTINF_ATTRIBUTE_RE = re.compile(r'([\w-]+)="([^"]*)"')
CHANNEL_BLOCK_RE = re.compile(
    r"(?P<block><channel\b(?P<open>[^>]*)>.*?</channel\s*>)", re.IGNORECASE | re.DOTALL
)
PROGRAMME_BLOCK_RE = re.compile(
    r"(?P<block><programme\b(?P<open>[^>]*)>.*?</programme\s*>)",
    re.IGNORECASE | re.DOTALL,
)
DISPLAY_NAME_RE = re.compile(r"<display-name\b[^>]*>(.*?)</display-name\s*>", re.IGNORECASE | re.DOTALL)
CCTV_MAIN_SUFFIXES = {
    "综合",
    "财经",
    "综艺",
    "中文国际",
    "体育",
    "电影",
    "国防军事",
    "电视剧",
    "纪录",
    "科教",
    "戏曲",
    "社会与法",
    "新闻",
    "少儿",
    "音乐",
    "农业农村",
}
PLAYLIST_NAME_OVERRIDES = {
    "cctv5p": ("cctv5+", "cctv5plus"),
    "cgtnen": ("cgtn英语",),
    "cgtnfrench": ("cgtn法语",),
    "cgtnsp": ("cgtn西班牙语",),
}


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


def get_attribute(tag: str, name: str) -> str | None:
    """Return a quoted attribute from an XML opening tag without reformatting it."""

    match = re.search(rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1", tag, re.IGNORECASE)
    return match.group(2) if match else None


def replace_attribute(tag: str, name: str, value: str) -> str:
    """Replace one quoted XML attribute, preserving the surrounding markup."""

    escaped = escape(value, {'"': "&quot;"})
    pattern = re.compile(rf"(\b{re.escape(name)}\s*=\s*)([\"']).*?\2", re.IGNORECASE)
    return pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{escaped}{match.group(2)}", tag, count=1)


def add_display_name(channel_block: str, display_name: str) -> str:
    """Append an alias display-name to cover players that match names, not IDs."""

    closing_match = re.search(r"</channel\s*>", channel_block, re.IGNORECASE)
    if not closing_match:
        raise ValueError("Invalid XMLTV channel block")
    display = f"<display-name>{escape(display_name)}</display-name>"
    return f"{channel_block[:closing_match.start()]}{display}{channel_block[closing_match.start():]}"


def normalise_channel_name(value: str) -> str:
    """Normalise superficial spelling differences without translating channel names."""

    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s_.\-()\[\]（）]+", "", value)


def playlist_identities(playlist: str) -> set[str]:
    """Read public channel metadata from an M3U without retaining stream URLs."""

    identities: set[str] = set()
    for line in playlist.splitlines():
        if not line.startswith("#EXTINF"):
            continue
        attributes = dict(EXTINF_ATTRIBUTE_RE.findall(line))
        label = line.rsplit(",", 1)[-1].strip()
        for value in (attributes.get("tvg-id"), attributes.get("tvg-name"), label):
            if value:
                identities.add(value.strip())
    return identities


def build_channel_lookup(xml: str) -> tuple[dict[str, str], dict[str, str]]:
    """Create name lookup and source channel opening blocks from XMLTV channel tags."""

    lookup: dict[str, set[str]] = defaultdict(set)
    channel_blocks: dict[str, str] = {}
    for match in CHANNEL_BLOCK_RE.finditer(xml):
        channel_id = get_attribute(match.group("open"), "id")
        if not channel_id:
            continue
        channel_blocks.setdefault(channel_id, match.group("block"))
        names = [channel_id, *DISPLAY_NAME_RE.findall(match.group("block"))]
        for name in names:
            if name.strip():
                lookup[normalise_channel_name(name)].add(channel_id)

    # Keep only unambiguous names: aliases must never guess between two EPG channels.
    return (
        {name: next(iter(ids)) for name, ids in lookup.items() if len(ids) == 1},
        channel_blocks,
    )


def resolve_playlist_name(name: str, lookup: dict[str, str]) -> str | None:
    """Match an M3U name to an unambiguous XMLTV channel ID."""

    candidate = normalise_channel_name(name)
    candidates = [candidate]
    stripped = candidate
    for suffix in ("av3a", "mcp", "hdr"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
            candidates.append(stripped)

    # Known provider spellings that differ from the XMLTV source names.
    for item in tuple(candidates):
        candidates.extend(PLAYLIST_NAME_OVERRIDES.get(item, ()))

    # Common official CCTV labels, e.g. CCTV1综合 -> CCTV1.
    match = re.fullmatch(r"cctv(\d+)([\u4e00-\u9fff]+)", candidate)
    if match and match.group(2) in CCTV_MAIN_SUFFIXES:
        candidates.append(f"cctv{match.group(1)}")
    if stripped == "cctv5+体育赛事":
        candidates.extend(("cctv5+", "cctv5plus"))

    for item in candidates:
        target = lookup.get(item)
        if target:
            return target
    return None


def apply_playlist_aliases(xml: str, playlist: str) -> tuple[str, dict[str, str]]:
    """Add XMLTV aliases so the supplied M3U's names can resolve programme data."""

    lookup, channel_blocks = build_channel_lookup(xml)
    aliases: dict[str, str] = {}
    for identity in playlist_identities(playlist):
        target = resolve_playlist_name(identity, lookup)
        if target and identity != target:
            aliases[identity] = target

    if not aliases:
        return xml, aliases

    programmes_by_channel: dict[str, list[str]] = defaultdict(list)
    for match in PROGRAMME_BLOCK_RE.finditer(xml):
        channel_id = get_attribute(match.group("open"), "channel")
        if channel_id:
            programmes_by_channel[channel_id].append(match.group("block"))

    additions: list[str] = []
    for alias, target in sorted(aliases.items(), key=lambda item: item[0].casefold()):
        channel_block = channel_blocks.get(target)
        if not channel_block:
            continue
        additions.append(add_display_name(replace_attribute(channel_block, "id", alias), alias))
        additions.extend(
            replace_attribute(programme, "channel", alias)
            for programme in programmes_by_channel.get(target, [])
        )

    closing_tag = "</tv>"
    closing_index = xml.rfind(closing_tag)
    if closing_index == -1:
        raise ValueError("Converted XMLTV document has no closing </tv> tag")
    additions_xml = "\n".join(additions)
    return f"{xml[:closing_index]}\n{additions_xml}\n{xml[closing_index:]}", aliases


def download_source() -> str:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "epg-sydney/1.0 (+https://github.com/BabyFoxy/epg-sydney)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def download_playlist(url: str) -> str:
    """Download an M3U source without ever printing its potentially private URL."""

    request = urllib.request.Request(url, headers={"User-Agent": "epg-sydney/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as error:
        raise RuntimeError("Playlist download failed") from error


def write_outputs(xml: str) -> None:
    OUTPUT_XML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_XML.write_text(xml, encoding="utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write(xml.encode("utf-8"))
    OUTPUT_GZIP.write_bytes(buffer.getvalue())


def main() -> None:
    source = download_source()
    converted, programmes, timestamps = convert_xml(source)
    aliases: dict[str, str] = {}
    if playlist_url := os.environ.get("PLAYLIST_URL"):
        converted, aliases = apply_playlist_aliases(converted, download_playlist(playlist_url))
    if not converted.lstrip().startswith("<?xml") or "<tv" not in converted[:1000]:
        raise ValueError("Downloaded content is not recognised as XMLTV")
    write_outputs(converted)
    print(
        f"Generated {OUTPUT_XML} ({OUTPUT_XML.stat().st_size:,} bytes) and "
        f"{OUTPUT_GZIP} ({OUTPUT_GZIP.stat().st_size:,} bytes); "
        f"converted {timestamps:,} timestamps in {programmes:,} programmes; "
        f"added {len(aliases):,} playlist aliases."
    )


if __name__ == "__main__":
    main()
