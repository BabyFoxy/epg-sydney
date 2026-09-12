# Sydney XMLTV EPG

This repository downloads [`live.fanmingming.cn/e.xml`](https://live.fanmingming.cn/e.xml)
and rewrites XMLTV `programme` `start` and `stop` timestamps for
`Australia/Sydney`. Daylight saving is handled by Python's IANA timezone data:

- AEST (`+1000`) in winter
- AEDT (`+1100`) in summer

The converter respects an explicit source offset such as `+0800`, `+00:00`, or
`Z`. If a source timestamp has no offset, it is interpreted as
`Asia/Shanghai`, matching the upstream feed's Chinese schedule.

When the repository's `PLAYLIST_URL` Actions secret is configured, the updater
also reads only the M3U `#EXTINF` metadata and creates XMLTV aliases for
unambiguous channel-name matches. The playlist URL and stream URLs are never
committed to this public repository.

The files are refreshed every two hours by GitHub Actions and can also be
refreshed with the **Run workflow** button.

## Player URLs

After GitHub Pages is enabled, use:

```text
https://babyfoxy.github.io/epg-sydney/epg.xml
https://babyfoxy.github.io/epg-sydney/epg.xml.gz
```

The always-available raw URL is:

```text
https://raw.githubusercontent.com/BabyFoxy/epg-sydney/main/docs/epg.xml
https://raw.githubusercontent.com/BabyFoxy/epg-sydney/main/docs/epg.xml.gz
```

## Local checks

```sh
python -m unittest discover -s tests -p 'test_*.py'
python convert_epg.py
```
