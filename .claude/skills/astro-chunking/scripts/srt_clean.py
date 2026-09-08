#!/usr/bin/env python3
"""SRT 逐字稿前處理：去序號/時間軸，合併字幕行，每換一分鐘留一個 [HH:MM:SS] 標記供 source_ts 回溯。

用法: python3 srt_clean.py input.srt > output.txt
"""
import re
import sys

TS_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}),\d{3}\s*-->")


def clean(path):
    out, last_min = [], None
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.isdigit():
            continue
        m = TS_RE.match(line)
        if m:
            ts = m.group(1)
            minute = ts[:5]
            if minute != last_min:
                out.append(f"\n[{ts}]")
                last_min = minute
            continue
        out.append(line)
    return "\n".join(out).lstrip()


if __name__ == "__main__":
    print(clean(sys.argv[1]))
