# baidu-pan-skill

**English** · [简体中文](README.zh.md)

Agent Skill + 零依赖脚本，**可靠下载百度网盘分享链接**——不用 BaiduPCS-Go、
不用扫码登录、不用手动导出 Cookie。

来自一次真实的 1.4GB 备份任务（分享链接 → 本地 → 极空间 NAS）：常规路子全挂了
——dlink 必报 `31362 sign error`、并发打不过账号级限速、百度 `md5` 字段根本不是
内容哈希、删除 API 被风控挡死。**所有真正能用的路都沉淀在这里。**

> 📖 **背景长文**：[《一次 1.4GB 备份引发的逆向》（CSDN）](https://blog.csdn.net/boyzhaotian/article/details/166349848)——完整死路记录、并发限速实验与协议破译过程

## 工作原理

```
本机 Chrome/Edge 登录态（macOS Keychain）
        │  bdpan_cookies.py     ← PBKDF2 + AES-CBC + 剥 32 字节域名哈希前缀
        ▼
   cookies.json（0600 权限）
        │  bdpan_share.py inspect/save   ← share/verify → 解析 locals.mset → 转存
        ▼
   自己网盘里的文件
        │  bdpan_download.py    ← 经典 PCS 端点，4MB 分块，断点续传账本
        ▼
   本地文件
        │  bdpan_verify.py      ← 大小 + 原子结构平铺 + ffprobe 时长三重校验
        ▼
   校验通过（之后交给 zs up / rclone / 随便什么）
```

**零 pip 依赖**——纯标准库（`urllib`、`sqlite3`、`hashlib`）；没装
`pycryptodome` 时 AES 自动回退到 `openssl` 命令行。

## 快速上手

```bash
# 1. 从已登录的浏览器提取 Cookie（macOS；首次运行会弹 Keychain 授权）
python3 scripts/bdpan_cookies.py --out /tmp/ck.json

# 2. 查看分享内容
python3 scripts/bdpan_share.py inspect "https://pan.baidu.com/s/1xxxx?pwd=ab12" --cookies /tmp/ck.json

# 3. 转存到自己网盘，然后断点续传下载（带自动校验）
python3 scripts/bdpan_share.py save "https://pan.baidu.com/s/1xxxx?pwd=ab12" \
    --cookies /tmp/ck.json --dest "/备份中转" > saved.json
python3 scripts/bdpan_download.py --cookies /tmp/ck.json --batch saved.json \
    --out ~/Downloads/share --verify

# 4. （或单独校验：大小 + 容器原子 + 时长）
python3 scripts/bdpan_verify.py ~/Downloads/share/视频.mov --expect-size 740378697 --expect-duration 3615

rm /tmp/ck.json   # Cookie 就是账号凭据——用完即删
```

- **文件夹分享全支持**：`save` 整目录转存并递归遍历子树，`--batch` 按
  `relpath` 在本地镜像目录结构（课程类分享就是整个目录的场景）
- 下载中断？原命令重跑即可——`*.dlstate.json` 账本记录已完成的 4MB 块，一块都不会重下
- **Cookie 中途过期？** macOS 上下载器自动重新从浏览器提取（最多 2 次），原块续传不丢进度
- 每块都校验 `Content-Range` 与请求区间一致——CDN 错位返回会立刻报错，绝不静默写坏文件

## 血泪经验表（本仓库存在的意义）

| 坑 | 真相 |
|----|------|
| `api/filemetas?dlink=1` 拿到的链接 | Web 会话必报 `31362 sign error`，回传 PANPSC Cookie 也没救。用经典端点：`d.pcs.baidu.com/rest/2.0/pcs/file?method=download&app_id=250528&path=…` + UA `pan.baidu.com` |
| "多开连接能不能跑赢限速？" | 不能——限速是**账号级聚合**（非会员 ~50KB/s，4/8/16 并发实测总量不变）。唯一解是 SVIP。几个 GB 的任务请按小时规划 |
| 用 `md5` 字段校验 | 那是百度**对象 key**（会出现在 dlink 路径里），大文件不是内容哈希。用 大小 + ISO-BMFF/QuickTime 原子平铺 + 时长交叉 校验 |
| 用完 API 删除中转文件 | `api/filemanager?opera=delete` → `errno 132` + authwidget 风控。请在客户端手动删 |
| Chrome Cookie 解密 | 新版 Chrome 明文前有 **32 字节 SHA256 域名哈希**，要剥掉；STOKEN 有两个域，取 `.pan.baidu.com` 的 |
| 极空间 `/znetdisk/*` 捷径 | 分享直下需要"百度NAS会员"（服务端 code 15 强制）；非会员的 `file/download` 任务 0 B/s 卡死 |

更多细节见 [`SKILL.md`](../SKILL.md)（踩坑记录 + 关键约束）。

## 作为 Agent Skill 使用

`SKILL.md` 遵循通用 agent-skill 规范（YAML frontmatter：`name`/`description`/
触发词/不适用 + 工作流场景）。安装到 DSH 风格技能目录：

```bash
mkdir -p ~/.dsh/skills/baidu-pan
cp SKILL.md ~/.dsh/skills/baidu-pan/
cp -R scripts ~/.dsh/skills/baidu-pan/
```

兼容任何读取 `SKILL.md` 的 agent 框架（Claude Code skills、zspace-cli 的
`zs skill` 规范、自建 harness）。与 [zspace-cli](https://github.com/skyzhao1223/zspace-cli)
配对即成完整的 网盘→NAS 备份流水线。

## 平台支持

| 平台 | Cookie 提取 | 下载/校验 |
|------|------------|-----------|
| macOS | ✅ Chrome / Edge / Brave（Keychain） | ✅ |
| Linux | ❌（Safe Storage 密码是固定串 `peanuts`——欢迎 PR，`bdpan_cookies.py` 里 ~10 行） | ✅（自备 cookies.json） |
| Windows | ❌（DPAPI / app-bound 加密——欢迎 PR） | ✅（自备 cookies.json） |

下载器、分享工具、校验器完全跨平台——只有 Cookie **提取**是 macOS 专属。
手动导出的 `cookies.json`（`{"BDUSS": "...", "STOKEN": "...", "BAIDUID": "..."}`）
在所有平台可用。

## 合规与安全

**用途边界。** 本项目定位是：在**你自己的机器**上、用**你自己的登录态**、
自动化备份**你有权处置的文件**。并且：

- **不绕过百度限速**——实测的账号级限速被如实遵守并写进文档（SVIP 是唯一
  提速途径，项目不提供也不暗示任何绕过手段）
- **不破解会员门槛**——凡遇官方付费门槛（如极空间 `/znetdisk` 分享直下的
  NAS 会员校验），如实记录"无法绕过"，不做规避
- **不分发任何第三方内容**——工具只在你自己的账号与设备之间搬运字节，
  不托管、不索引、不分享资源
- **遇风控即停**——验证码、删除安全校验等风控介入时停止自动化、交还人工

**禁止用于**：批量爬取、聚合转售下载通道、下载你无权处置的内容、任何形式的
商业化分发。使用百度网盘服务仍受百度用户协议约束——自动化可能触发账号风控
（验证码、功能限制），该风险由使用者自担。

**凭据安全。** `cookies.json` 是完整账号凭据——脚本落盘 0600、从不打印值、
除百度官方端点外不向任何第三方发送；用完请删除。

**互操作性文档。** 仓库内的 API 笔记是对公开可达端点行为的观察记录，仅用于
个人互操作；接口非官方、随时可能失效，提 issue 请附确切 errno。

**投诉与联系。** 权利人或平台方如有异议，请联系维护者
`skyzhao1223@users.noreply.github.com`，合理诉求会被及时处理。

**可用性保障。** 每个 GitHub Release 自带源码归档；维护者另在站外保留完整
历史的 `git bundle` 镜像（bundle 可直接 `git clone`）。若本仓库某天不可访问，
可通过维护者或相关社区渠道获取恢复点。

## 开发

```bash
pip install "ruff>=0.11" pytest
ruff check scripts tests
pytest -q          # 全离线：openssl 合成 AES 夹具 + 假 fetcher
```

欢迎认领 issue。特别想要：Linux/Windows Cookie 提取、`--svip` 多连接模式
（仅 SVIP 账号有效，会员下确实能线性加速）、文件夹分享递归下载。

## 许可证

MIT
