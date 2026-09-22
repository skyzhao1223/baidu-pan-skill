#!/usr/bin/env python3
"""Resumable downloader for files in your own Baidu NetDisk.

Uses the legacy PCS endpoint, which — unlike ``api/filemetas`` dlinks — does
NOT fail with the dreaded ``31362 sign error``:

    GET https://d.pcs.baidu.com/rest/2.0/pcs/file?method=download
        &app_id=250528&path=<urlencoded remote path>
    headers: User-Agent: pan.baidu.com, Cookie: BDUSS=...; ..., Range: bytes=a-b

Speed reality check: throttling is ACCOUNT-WIDE (~50 KB/s aggregated for
non-SVIP; 4/8/16 parallel range requests do not add up). This tool therefore
downloads sequentially and focuses on being unkillable: 4 MB chunks, a
``*.dlstate.json`` resume ledger, per-chunk retries with capped backoff.
Plan multi-GB jobs in hours, or use an SVIP account.

Integrity: Baidu's ``md5`` list field is an object key, NOT a content hash.
Verify with size + container structure + duration instead (bdpan_verify.py).

Usage:
    python3 bdpan_download.py --cookies cookies.json \
        --remote "/备份中转/视频.mov" [--size 740378697] [--out DIR] [--name x.mov]
    python3 bdpan_download.py --cookies cookies.json --batch saved.json --out DIR
        # saved.json = output of `bdpan_share.py save` ({saved:[{path,size,name}]})
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bdpan_common import DOWNLOAD_UA, load_cookies  # noqa: E402

PCS_URL = "https://d.pcs.baidu.com/rest/2.0/pcs/file"
CHUNK = 4 * 1024 * 1024
MAX_RETRY = 200

Fetcher = Callable[[str, int, int], bytes]


def make_fetcher(cookies: dict[str, str], timeout: int = 120) -> Fetcher:
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

    def fetch(remote: str, start: int, end: int) -> bytes:
        url = (PCS_URL + "?method=download&app_id=250528&path="
               + urllib.parse.quote(remote))
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", DOWNLOAD_UA)
        req.add_header("Cookie", cookie_header)
        req.add_header("Range", f"bytes={start}-{end}")
        req.add_header("Accept-Encoding", "identity")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 206):
                raise OSError(f"HTTP {resp.status} for range {start}-{end}")
            return resp.read()

    return fetch


def probe_size(cookies: dict, remote: str, attempts: int = 3) -> int:
    """GET the first byte and read the total from Content-Range.

    Retries a few times: Baidu's auth backend occasionally returns transient
    403/31045 even with perfectly valid cookies.
    """
    url = PCS_URL + "?method=download&app_id=250528&path=" + urllib.parse.quote(remote)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    cr = ""
    last_err = ""
    for attempt in range(attempts):
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", DOWNLOAD_UA)
        req.add_header("Cookie", cookie_header)
        req.add_header("Range", "bytes=0-0")
        req.add_header("Accept-Encoding", "identity")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                cr = resp.headers.get("Content-Range", "")
        except urllib.error.HTTPError as e:
            cr = e.headers.get("Content-Range", "") if e.headers else ""
            if e.code == 403:
                last_err = e.read()[:200].decode("utf-8", "replace")
        except OSError as e:
            last_err = str(e)[:200]
        if cr:
            break
        time.sleep(2 + attempt * 2)
    else:
        if "31045" in last_err or "not exists" in last_err or last_err:
            raise SystemExit(
                f"PCS endpoint rejected auth after {attempts} tries: {last_err}\n"
                "cookies expired/insufficient — re-run bdpan_cookies.py")
    m = cr.rsplit("/", 1)
    if len(m) != 2 or not m[1].isdigit():
        raise SystemExit(f"cannot probe size of {remote!r} (Content-Range={cr!r}) — pass --size")
    return int(m[1])


def plan_chunks(size: int, chunk: int = CHUNK) -> int:
    return (size + chunk - 1) // chunk


def load_state(state_path: Path, total_chunks: int, size: int) -> set:
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text())
            if st.get("total_chunks") == total_chunks and st.get("size") == size:
                return set(st.get("done", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_state(state_path: Path, done: set, total_chunks: int, size: int) -> None:
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": sorted(done), "total_chunks": total_chunks,
                               "size": size}))
    os.replace(tmp, state_path)


def download_one(fetch: Fetcher, remote: str, out_dir: Path, name: str,
                 size: int | None = None, chunk: int = CHUNK,
                 cookies: dict | None = None,
                 log: Callable[[str], None] = print) -> Path:
    """Download one remote file with resume; returns the local path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / name
    state_path = out_dir / (name + ".dlstate.json")
    if size is None:
        if not cookies:
            raise ValueError("pass size= or cookies= (needed to probe the remote size)")
        size = probe_size(cookies, remote)
    total_chunks = plan_chunks(size, chunk)
    done = load_state(state_path, total_chunks, size)

    if not local.exists() or local.stat().st_size != size:
        with open(local, "wb") as fh:      # preallocate (sparse on APFS/ext4)
            fh.truncate(size)
    if len(done) >= total_chunks and local.stat().st_size == size:
        log(f"[{name}] already complete ({size} B)")
        return local

    log(f"[{name}] resuming at {len(done)}/{total_chunks} chunks "
        f"({size} B total)")
    t0 = time.time()
    with open(local, "r+b") as fh:
        i = 0
        while i < total_chunks:
            if i in done:
                i += 1
                continue
            start = i * chunk
            end = min(start + chunk - 1, size - 1)
            expect = end - start + 1
            ok = False
            for attempt in range(MAX_RETRY):
                try:
                    data = fetch(remote, start, end)
                    if len(data) == expect:
                        fh.seek(start)
                        fh.write(data)
                        fh.flush()
                        done.add(i)
                        if len(done) % 10 == 0 or len(done) == total_chunks:
                            save_state(state_path, done, total_chunks, size)
                            el = time.time() - t0
                            spd = len(done) * chunk / el / 1024 if el else 0
                            eta_h = ((total_chunks - len(done)) * chunk
                                     / max(spd * 1024, 1) / 3600)
                            log(f"[{name}] {len(done)}/{total_chunks} "
                                f"({100*len(done)/total_chunks:.1f}%) "
                                f"~{spd:.0f}KB/s eta {eta_h:.1f}h")
                        ok = True
                        break
                    log(f"[{name}] chunk {i}: short read {len(data)}/{expect}, retry")
                except KeyboardInterrupt:
                    save_state(state_path, done, total_chunks, size)
                    log(f"[{name}] interrupted — state saved at {len(done)}/{total_chunks}")
                    raise
                except Exception as e:  # network hiccups, 403 bursts, timeouts
                    log(f"[{name}] chunk {i}: {type(e).__name__} {str(e)[:80]}")
                time.sleep(min(30, 2 + attempt * 2))
            if not ok:
                save_state(state_path, done, total_chunks, size)
                raise OSError(f"gave up on chunk {i} after {MAX_RETRY} attempts")
            i += 1
    save_state(state_path, done, total_chunks, size)
    actual = local.stat().st_size
    if actual != size:
        raise OSError(f"size mismatch: {actual} != {size}")
    log(f"[{name}] DONE {size} B in {(time.time()-t0)/60:.1f} min")
    return local


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cookies", default="cookies.json")
    ap.add_argument("--remote", help="absolute path inside your pan")
    ap.add_argument("--size", type=int, default=None, help="skip probing when known")
    ap.add_argument("--name", default=None, help="local file name (default: remote basename)")
    ap.add_argument("--batch", help="JSON with {saved:[{path,size,name}]} (bdpan_share.py save)")
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--chunk-mb", type=int, default=4)
    args = ap.parse_args()

    if bool(args.remote) == bool(args.batch):
        raise SystemExit("pass exactly one of --remote / --batch")

    cookies = load_cookies(args.cookies)
    fetch = make_fetcher(cookies)
    out_dir = Path(args.out).expanduser()
    chunk = args.chunk_mb * 1024 * 1024

    jobs = []
    if args.remote:
        name = args.name or Path(args.remote).name
        jobs.append((args.remote, name, args.size))
    else:
        data = json.loads(Path(args.batch).read_text())
        for item in data.get("saved", []):
            jobs.append((item["path"], item.get("name") or Path(item["path"]).name,
                         item.get("size")))

    failures = 0
    for remote, name, size in jobs:
        try:
            download_one(fetch, remote, out_dir, name, size=size, chunk=chunk, cookies=cookies)
        except (OSError, SystemExit) as e:
            print(f"FAILED {remote}: {e}", file=sys.stderr)
            failures += 1
    print(f"ALL_DONE ({len(jobs)-failures}/{len(jobs)} ok)" if not failures
          else f"{failures} FAILED — re-run to resume", file=sys.stderr if failures else sys.stdout)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
