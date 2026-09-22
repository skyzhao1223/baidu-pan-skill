# baidu-pan-skill

Agent Skill + zero-dependency scripts to **download Baidu NetDisk (百度网盘) share
links reliably** — no BaiduPCS-Go, no QR login, no manual cookie export.

Born from a real 1.4 GB backup job (share link → local → ZSpace NAS) where the
obvious approaches all failed: `dlink` died with `31362 sign error`, parallel
connections didn't beat the account-wide throttle, Baidu's `md5` field turned
out to be an object key rather than a content hash, and the delete API is
risk-control gated. Everything that *did* work is captured here.

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

# 3. transfer it into your own pan, then download (resumable)
python3 scripts/bdpan_share.py save "https://pan.baidu.com/s/1xxxx?pwd=ab12" \
    --cookies /tmp/ck.json --dest "/备份中转" > saved.json
python3 scripts/bdpan_download.py --cookies /tmp/ck.json --batch saved.json --out ~/Downloads/share

# 4. verify integrity (size + container atoms + duration)
python3 scripts/bdpan_verify.py ~/Downloads/share/视频.mov --expect-size 740378697 --expect-duration 3615

rm /tmp/ck.json   # cookies are credentials — delete when done
```

Interrupted? Re-run the same download command — `*.dlstate.json` tracks
completed 4 MB chunks, nothing is re-downloaded.

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

- For **personal backup of files you have rights to**; respects Baidu's
  throttling rather than circumventing it (no protocol spoofing beyond a
  desktop UA on the official download endpoint)
- `cookies.json` is a full account credential — the scripts chmod it 0600 and
  never print values; delete it after use
- Baidu's web API is unofficial and changes without notice; pin failures to
  exact errnos in issues

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
