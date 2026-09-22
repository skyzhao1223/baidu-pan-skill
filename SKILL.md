---
name: baidu-pan
description: Use when 用户想下载/备份百度网盘分享链接的文件、转存分享到自己网盘、
  校验百度网盘下载的文件完整性，或排查百度网盘限速/31362 sign error/errno 132 等问题。
  零依赖脚本（纯 stdlib + openssl），从本机 Chrome/Edge 登录态提取 Cookie，无需扫码登录。
  触发词: 百度网盘下载、网盘分享链接、提取码、转存、dupan、baidu pan、百度限速、
  备份网盘文件到本地/NAS、31362、dlink 失败。
  不适用: 阿里云盘/115/迅雷等其他网盘（无对应实现）；极空间 NAS 侧文件操作用 zspace-nas
  skill；需要 SVIP  speeds 的场景本 skill 无法绕过账号级限速。
---

# baidu-pan — 百度网盘分享下载与校验

从百度网盘分享链接（`https://pan.baidu.com/s/1xxx?pwd=yyyy`）把文件可靠地拉到本地：
Cookie 自动提取 → 分享解析 → 转存 → 断点续传下载 → 结构校验。全程纯 stdlib，
不需要 BaiduPCS-Go、不需要扫码登录、不需要用户手动导出 Cookie。

## Prerequisites

- **macOS**，本机 Chrome/Edge 已登录过 `pan.baidu.com`（skill 从 Keychain 的
  "Chrome Safe Storage" 解密浏览器 Cookie；首次运行 macOS 可能弹 Keychain 授权）
- `openssl` CLI（macOS 自带）或 `pycryptodome`（可选，装了更快）
- 可选：`ffprobe`（ffmpeg）用于时长交叉校验
- Python ≥ 3.9
- ⚠️ Cookie 是账号凭据：`cookies.json` 已按 0600 落盘，**用完即删**，不要进 git/日志

## 命令速查

所有脚本在 `scripts/` 下，无安装步骤，直接 `python3 <script> --help`。

| 步骤 | 命令 | 输出 |
|------|------|------|
| 1. 提取 Cookie | `python3 scripts/bdpan_cookies.py --out /tmp/ck.json` | 0600 JSON；缺 BDUSS/STOKEN/BAIDUID 会报错并提示先登录 |
| 2a. 只看分享内容 | `python3 scripts/bdpan_share.py inspect "<url>" --cookies /tmp/ck.json` | JSON：shareid、files[]（fs_id/size/duration/md5_object_key） |
| 2b. 转存到自己网盘 | `python3 scripts/bdpan_share.py save "<url>" --cookies /tmp/ck.json --dest "/备份中转"` | JSON：saved[]（path/size/fs_id/relpath/duration），可直接喂给下载；**文件夹分享自动递归整树** |
| 3. 断点续传下载 | `python3 scripts/bdpan_download.py --cookies /tmp/ck.json --batch saved.json --out ~/Downloads/x --verify` 或 `--remote "/路径/文件.mov" --size N` | 分块下载 + `*.dlstate.json` 账本；中断重跑即续传；`--verify` 下载完自动三重校验；每块校验 Content-Range；macOS 上 Cookie 过期自动重提（≤2 次） |
| 4. 校验 | `python3 scripts/bdpan_verify.py FILE --expect-size N --expect-duration S` | 大小 + 容器原子结构 + ffprobe 时长三重校验，退出码 0/1 |

分享文件如果**已在用户自己网盘里**（用 2a 的 fs_id 对不上时先问用户或 `inspect`
后对照），可跳过转存直接 `--remote` 下载。

## 工作流场景

### 场景 A：下载分享链接的 N 个文件并备份到 NAS（极空间）

1. 提取 Cookie → `inspect` 拿到文件清单，**把总大小和预估耗时报给用户**
   （非会员 ~50KB/s：1GB ≈ 6 小时；用户是 SVIP 则秒级满速）——耗时超预期时先问
2. `save --dest "/备份中转-xxx"`（转存目录名带上用途，方便事后清理）
3. 下载放**后台任务**跑（几小时级），加 `--verify` 让每个文件下载完自动校验（batch 自带 duration 元数据）
4. 校验过的文件用 zspace-nas skill（`zs up`，大文件走分片）上传 NAS，
   `zs info` 对字节数
5. 收尾提醒用户：网盘中转目录可在百度客户端删除（**API 删除会被风控挡，见踩坑**）；
   本地副本是否保留由用户决定；`rm` 掉 cookies.json

### 场景 B：只要转存，不下载

Cookie → `save`。报告 saved[] 的路径与大小即可。

### 场景 C：下载中断恢复

`.dlstate.json` 在原输出目录里 → 原命令重跑即自动续传（chunk 级，不重下已完成块）。

## 关键约束（务必遵守）

1. **不要走 dlink**：`api/filemetas?dlink=1` 拿到的链接必报 `31362 sign error`
   （回传 PANPSC Cookie 也没用）。唯一可靠通道是下载脚本用的经典 PCS 端点。
2. **限速是账号级聚合**：非会员 ~50KB/s，多线程/多连接总量不变，别浪费力气并发；
   唯一解是 SVIP。给用户报 ETA 时按实测速率算，不要按单连接速率乐观外推。
3. **`md5` 字段不是内容哈希**：大文件的 `md5` 是百度对象 key（会出现在 dlink 路径
   里）。校验用 size + 容器结构 + duration（`bdpan_verify.py` 已封装）。
4. **删除 API 有风控**：`api/filemanager?opera=delete` 返回 errno 132 + authwidget，
   自动化删除此路不通——让用户在客户端/网页手动删。
5. **captcha**：`share/verify` 或分享页返回验证码（脚本会报 `captcha` errno）时，
   让用户在浏览器打开一次该链接再重试。
6. Cookie 失效特征：`gettemplatevariable` errno -6 或 loginstate≠1 → 重新提取；
   下载中遇认证类 403（31045 等）时，macOS 上下载器会自动重提 Cookie 续命（≤2 次），
   仍失败才停——非 macOS 平台直接失败并保存进度，问用户要新 cookies.json。

## 踩坑记录（逆向实测，2026-09）

- Chrome 新版 Cookie 明文前有 **32 字节 SHA256 域名哈希前缀**，解密后要剥掉
  （脚本已处理：前 32 字节含非可打印字符即剥离）
- STOKEN 存在 `.pan.baidu.com` 和 `.passport.baidu.com` 两个域，**必须取 pan 域的**
- `surl` = 分享 URL `/s/` 后**去掉前导 1**；verify API 用 surl，页面 URL 用带 1 的完整串
- 分享页数据在 `locals.mset({...});` 里，一次拿全 shareid/uk/file_list（含 duration）
- 极空间 NAS 侧的 `/znetdisk/*` 百度集成：分享直下需要"百度 NAS 会员"（服务端
  code 15 强制），非会员的 `file/download` 任务会以 0 B/s 卡死——不要尝试 NAS 旁路
- 下载器进度行的速率若按"累计块数/本次运行时间"计算，续传时会虚高（v0.2 已修：只统计本次会话字节数）——旧版本 ETA 报给用户前留意

## 故障排查

| 症状 | 处置 |
|------|------|
| `missing required cookies` | 浏览器没登录百度网盘 → 登录后重跑 cookies |
| Keychain 弹窗被拒 | 重跑并在弹窗点"始终允许"，或 `security find-generic-password -s "Chrome Safe Storage" -w` 手动验证 |
| verify 失败但大小对 | 容器原子不平铺 → 下载错位，删掉 .dlstate.json 和文件重下 |
| 持续 403 / `credentials rejected` | macOS 会先自动重提 Cookie；仍失败 = 风控或浏览器登出 → 让用户浏览器打开网盘一次再重跑（账本还在） |
| 速度突然归零 | 账号级风控（当日下载量过大）→ 隔天续传，账本还在 |
