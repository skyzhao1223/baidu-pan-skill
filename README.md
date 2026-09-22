# baidu-pan-skill

Agent Skill + zero-dependency scripts to **download Baidu NetDisk (百度网盘) share
links reliably** — no BaiduPCS-Go, no QR login, no manual cookie export.

Born from a real 1.4 GB backup job (share link → local → ZSpace NAS) where the
obvious approaches all failed: `dlink` died with `31362 sign error`, parallel
connections didn't beat the account-wide throttle, Baidu's `md5` field turned
out to be an object key rather than a content hash, and the delete API is
risk-control gated. Everything that *did* work is captured here.

> 📖 **Background story (zh)**: [一次 1.4GB 备份引发的逆向 — the full reverse-engineering write-up](https://blog.csdn.net/boyzhaotian/article/details/166349848)

## How it works

```
Chrome/Edge login state (macOS Keychain)
        │  bdpan_cookies.py     ← PBKDF2 + AES-CBC + 32-byte domain-hash strip
        ▼
   cookies.json (0600)
        │  bdpan_share.py inspect/save   ← share/verify → locals.mset parse → transfer
        ▼
   files in your own pan
        │  bdpan_download.py    ← legacy PCS endpoint, 4MB chunks, resume ledger
        ▼
   local files
        │  bdpan_verify.py      ← size + atom tiling + ffprobe duration
        ▼
   verified (then hand off to zs up / rclone / whatever)
```

**Zero pip dependencies** — pure stdlib (`urllib`, `sqlite3`, `hashlib`);
AES falls back to the `openssl` CLI when `pycryptodome` isn't installed.

## Quick start

```bash
# 1. grab cookies from your logged-in browser (macOS; Keychain prompt on first run)
python3 scripts/bdpan_cookies.py --out /tmp/ck.json

# 2. see what's in a share link
python3 scripts/bdpan_share.py inspect "https://pan.baidu.com/s/1xxxx?pwd=ab12" --cookies /tmp/ck.json

# 3. transfer it into your own pan, then download (resumable, auto-verified)
python3 scripts/bdpan_share.py save "https://pan.baidu.com/s/1xxxx?pwd=ab12" \
    --cookies /tmp/ck.json --dest "/备份中转" > saved.json
python3 scripts/bdpan_download.py --cookies /tmp/ck.json --batch saved.json \
    --out ~/Downloads/share --verify

# 4. (or verify separately: size + container atoms + duration)
python3 scripts/bdpan_verify.py ~/Downloads/share/视频.mov --expect-size 740378697 --expect-duration 3615

rm /tmp/ck.json   # cookies are credentials — delete when done
```

- **Folder shares work**: `save` transfers whole directories and walks the
  subtree; `--batch` mirrors the tree locally via `relpath`.
- **Interrupted?** Re-run the same download command — `*.dlstate.json` tracks
  completed 4 MB chunks, nothing is re-downloaded.
- **Cookies expired mid-run?** On macOS the downloader re-extracts them from
  the browser automatically (up to 2×) and resumes the same chunk.
- Every chunk's `Content-Range` is validated against the requested window —
  a mis-serving CDN fails loudly instead of corrupting the file.

## The hard-won knowledge (why this repo exists)

| Trap | Reality |
|------|---------|
| `api/filemetas?dlink=1` links | Always `31362 sign error` for web sessions, even replaying the `PANPSC` cookie. Use the legacy endpoint: `d.pcs.baidu.com/rest/2.0/pcs/file?method=download&app_id=250528&path=…` with UA `pan.baidu.com` |
| "Maybe parallel connections beat the throttle?" | No — throttling is **account-wide aggregate** (~50 KB/s non-SVIP; measured identical at 4/8/16 connections). Only SVIP helps. Plan multi-GB jobs in hours |
| Verify with the `md5` field | It's Baidu's **object key** (appears in dlink paths), not a content hash for large files. Verify with size + ISO-BMFF/QuickTime atom tiling + duration cross-check instead |
| Delete via API afterwards | `api/filemanager?opera=delete` → `errno 132` + authwidget (risk control). Delete manually in the app |
| Chrome cookie decryption | Newer Chrome prefixes plaintext with a **32-byte SHA256 domain hash** — strip it. `STOKEN` exists under two hosts; the `.pan.baidu.com` one is the right one |
| ZSpace NAS `/znetdisk/*` shortcut | Share-direct-download requires 百度NAS会员 (server-enforced, code 15); non-VIP `file/download` tasks stall at 0 B/s |

More detail lives in [`SKILL.md`](SKILL.md) (踩坑记录 + 关键约束 sections).

## As an Agent Skill

`SKILL.md` follows the common agent-skill convention (YAML frontmatter with
`name`/`description`/触发词/不适用 + workflow scenarios). Install into
[DSH](https://github.com/deepseek-ai/dsh)-style skill directories:

```bash
mkdir -p ~/.dsh/skills/baidu-pan
cp SKILL.md ~/.dsh/skills/baidu-pan/
cp -R scripts ~/.dsh/skills/baidu-pan/
```

Works with any agent framework that reads `SKILL.md` (Claude Code skills,
zspace-cli's `zs skill` convention, custom harnesses). The skill pairs
naturally with [zspace-cli](https://github.com/skyzhao1223/zspace-cli) for
pan→NAS backup pipelines.

## Platform support

| Platform | Cookies | Download |
|----------|---------|----------|
| macOS | ✅ Chrome / Edge / Brave (Keychain) | ✅ |
| Linux | ❌ (Safe Storage password is the fixed string `peanuts` — PR welcome, ~10 lines in `bdpan_cookies.py`) | ✅ (bring your own cookies.json) |
| Windows | ❌ (DPAPI / app-bound encryption — PR welcome) | ✅ (bring your own cookies.json) |

The downloader, share tools and verifier are fully cross-platform — only cookie
*extraction* is macOS-specific. A manually exported `cookies.json`
(`{"BDUSS": "...", "STOKEN": "...", "BAIDUID": "..."}`) works everywhere.

## Legal & safety notes

**Intended use.** Personal backup automation for files **you have the rights
to**, driven by **your own login state** on **your own machine**. This project:

- does **not** circumvent Baidu's speed throttling — measured account-level
  limits are respected and documented as-is (SVIP is noted as the only
  speed-up, no bypass is provided or implied)
- does **not** bypass membership gates — where an official channel requires
  paid membership (e.g. the ZSpace `/znetdisk` share transfer), that is
  documented as "gated", not worked around
- does **not** host, index, or distribute any third-party content; it moves
  bytes between your own accounts and devices
- stops and defers to the human whenever risk control intervenes (captcha,
  delete verification)

**Not for:** mass/industrial scraping, re-sharing or reselling access,
downloading content you don't have rights to, or any monetized redistribution.
Using Baidu NetDisk's services remains subject to Baidu's own terms —
automation may trigger account-level risk controls (captchas, feature
limits); that risk is borne by the user.

**Credentials.** `cookies.json` is a full account credential — the scripts
chmod it 0600 and never print values; delete it after use. Nothing is ever
transmitted anywhere except to Baidu's own endpoints.

**Interoperability documentation.** The API notes here describe observed
behavior of publicly reachable endpoints for personal interoperability; they
are unofficial and may break at any time. Pin failures to exact errnos in
issues.

**Takedown / concerns.** Rights holders or platform representatives with a
concern should contact the maintainer at
`skyzhao1223@users.noreply.github.com` — legitimate requests are answered
promptly.

**Availability.** Every GitHub Release ships auto-generated source archives.
The maintainer additionally keeps off-platform `git bundle` mirrors of the
full history (a bundle is directly `git clone`-able); if this repository ever
disappears, ask the maintainer or check the linked community channels for a
restore point.

## Development

```bash
pip install "ruff>=0.11" pytest
ruff check scripts tests
pytest -q          # fully offline: synthetic AES fixtures via openssl, fake fetchers
```

Ideas welcome — see open issues. Especially: Linux/Windows cookie extraction,
`--svip` multi-connection mode (useful only for SVIP accounts, where it *does*
scale), album/folder-share recursion.

## License

MIT
