"""
range_file.py（v0.7.0）— 支援 HTTP Range 的檔案回應（影片串流、拖曳進度條都靠它）。

- 只支援單一範圍（bytes=a-b、bytes=a-、bytes=-n）；多範圍或格式錯誤的 Range 一律忽略、回整個檔案（RFC 7233 允許）。
- 範圍超出檔案 → 416 並帶 `Content-Range: bytes */size`。
- 分塊讀檔（1 MiB），瀏覽器中途斷線（拖曳進度條、關閉播放器）時產生器會被關閉、檔案立即釋放。
- 同步產生器由 Starlette 放到 threadpool 執行，不阻塞 event loop。
"""
import os
import re
from typing import Iterator, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

CHUNK = 1024 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """回傳 (start, end)（含頭含尾）；沒有 / 不支援的 Range 回 None；超出範圍拋 RangeNotSatisfiable。"""
    if not header:
        return None
    m = _RANGE_RE.match(header.strip().lower())
    if not m:
        return None                      # 多範圍、非 bytes 單位、格式錯誤：忽略
    s, e = m.group(1), m.group(2)
    if s == "" and e == "":
        return None
    if s == "":                          # 尾端 n 位元組
        n = int(e)
        if n == 0:
            raise RangeNotSatisfiable()
        return max(size - n, 0), size - 1
    start = int(s)
    if e != "" and int(e) < start:
        return None                      # a > b 視為無效，忽略
    if start >= size:
        raise RangeNotSatisfiable()
    end = int(e) if e != "" else size - 1
    return start, min(end, size - 1)


def _iter_file(path: str, start: int, length: int) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk


def range_response(path: str, media_type: str, range_header: Optional[str], head_only: bool = False,
                   cache_control: str = "private, no-cache") -> Response:
    try:
        size = os.stat(path).st_size
    except OSError:
        raise HTTPException(status_code=404, detail="檔案不存在（可能已移動、刪除或磁碟離線）")
    base = {"Accept-Ranges": "bytes", "Cache-Control": cache_control}
    try:
        rng = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={**base, "Content-Range": f"bytes */{size}"})
    if rng is None:
        start, end, status = 0, size - 1, 200
        headers = {**base, "Content-Length": str(size)}
    else:
        start, end = rng
        status = 206
        headers = {**base, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"}
    if head_only or size == 0:
        return Response(status_code=status, media_type=media_type, headers=headers)
    return StreamingResponse(_iter_file(path, start, end - start + 1), status_code=status,
                             media_type=media_type, headers=headers)
