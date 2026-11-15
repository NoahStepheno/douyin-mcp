<div align="center">
  <h1>douyin-mcp</h1>
  <p><strong>让 AI 同时读懂你的抖音创作数据和视频内容</strong></p>
  <p>本地运行 · 音轨文案按需提取 · 数据可追溯 · 面向个人创作者的 MCP Server</p>

  <p>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+"></a>
    <img src="https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-333333?logo=apple&amp;logoColor=white" alt="macOS and Windows">
    <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Compatible-22C55E" alt="MCP Compatible"></a>
    <a href="https://github.com/jlowin/fastmcp"><img src="https://img.shields.io/badge/FastMCP-Powered-FF6B35" alt="FastMCP Powered"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-8B5CF6" alt="AGPL-3.0"></a>
    <a href="https://github.com/Kuhakucai/douyin-mcp/stargazers"><img src="https://img.shields.io/github/stars/Kuhakucai/douyin-mcp?style=flat&amp;logo=github&amp;label=Stars" alt="GitHub Stars"></a>
  </p>

  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="docs/BEGINNER_AGENT_GUIDE.md">新手教程</a> ·
    <a href="#核心能力">核心能力</a> ·
    <a href="#视频文案提取用到时再加载">视频文案</a> ·
    <a href="#推荐用法">推荐用法</a> ·
    <a href="#工作原理">工作原理</a> ·
    <a href="#安全合规与许可">安全合规</a>
  </p>
</div>

![douyin-mcp：从创作者数据到 AI 分析](assets/douyin-mcp-hero.svg)

> 第一次接触开源、编程或 MCP？可以先看[新手教程：只要会给 Agent 下指令](docs/BEGINNER_AGENT_GUIDE.md)，直接把一句话发给 Agent，让它完成安装和配置。

> [!IMPORTANT]
> **这是非官方社区工具。** 本项目未获抖音或其关联公司授权、认可或背书。项目使用 Playwright 操作浏览器；即使只读取本人账号中真实可见的数据，也可能违反平台条款或触发账号风控。使用前请阅读[平台合规与非官方声明](PLATFORM_COMPLIANCE.md)，确认已取得所需授权，并先执行风险确认。AGPL 只许可项目代码，不授予任何平台访问权、数据权或商标权。

## 一分钟了解

`douyin-mcp` 在你的电脑上复用专用 Chrome 登录状态，将抖音创作者中心页面中真实可见的作品、经营指标和公开视频音轨文案保存到本地 SQLite，再通过 MCP 提供给支持 MCP 的 AI Agent。AI 不仅能看到“这条视频表现如何”，还能结合“视频具体讲了什么”进行分析和复盘。

| | |
|---|---|
| 📊 **读取真实可见数据**<br>增量同步作品列表、播放、点赞、评论、分享、收藏、完播率和涨粉等页面可见指标。 | 🎙️ **提取视频音轨文案**<br>将公开视频中的说话内容转成带时间戳的本地文案，供 AI 理解选题、钩子、结构和观点。 |
| 🧠 **结合内容与数据分析**<br>对比视频讲了什么、怎么讲以及最终表现，生成更有依据的内容复盘。 | ⚡ **文案按需加载**<br>启用后不处理全部历史视频；只预热近期内容，分析缺失文案时自动补齐。 |
| 🧾 **结论附带证据**<br>返回采集时间、缓存新鲜度、字段覆盖率、缺失原因和质量警告，不用猜测值填空。 | 🔒 **登录凭证留在本地**<br>Cookie 与浏览器状态保存在专用 profile 中，MCP 不向 Agent 返回认证材料。 |

它解决的是一个具体问题：

```text
抖音创作者中心（指标 + 公热视频音轨） → 本地结构化数据 → MCP → AI Agent
```

视频文案采用**按需加载**，而不是首次启动就批量处理全部历史视频。这样能更快完成首次同步，减少不必要的媒体下载、CPU 占用和本地存储；当用户真正分析某条视频时，缺失文案会自动进入后台提取队列。

当前范围为 **macOS 或 Windows、本机运行、单用户、单抖音账号**。本项目不提供多账号托管、云端采集、数据转售、未公开接口抓取，或绕过登录、安全验证、权限及风控的能力。

## 快速开始

### 环境要求

- macOS 或 Windows 10/11
- Python 3.11 或更高版本
- Google Chrome
- 一个支持 MCP 和终端操作的 Agent

视频文案是首次安装后的可选功能；需要时只要告诉 Agent“启用视频文案”，由它说明额外依赖、下载和磁盘占用，并在取得同意后完成配置。

### 推荐：让 Agent 完成安装和配置

用户只需要对 Agent 说：

```text
帮我克隆并安装配置 https://github.com/Kuhakucai/douyin-mcp.git 项目
```

Agent 必须先阅读本 README，并自行完成以下步骤，而不是要求用户逐条输入命令：

1. 检查 macOS/Windows、Python 3.11+ 和 Google Chrome；缺少系统级前置条件时，说明原因并先征得用户同意再安装。
2. 克隆仓库后，macOS 运行 `bash ./easy-install.sh`；Windows 运行 `powershell -ExecutionPolicy Bypass -File .\easy-install.ps1`。脚本仅创建项目专用 `.venv`、安装基础依赖、生成 `.env`、初始化数据库和执行诊断，不修改全局 Python 或系统 PATH。
3. 不假定 `douyin-mcp` 已在全局 PATH：macOS 使用 `./.venv/bin/douyin-mcp`，Windows 使用 `.\.venv\Scripts\douyin-mcp.exe`。运行 `doctor` 并确认 `ready=true`；失败时先诊断和修复。
4. 运行 `init`，读取输出的 `mcp_config`。展示平台风险并取得用户明确确认后，执行 `acknowledge-platform-risk --yes`；随后将完整的 `mcp_config` 写入用户当前使用的 MCP 客户端，包括 Codex、Claude Code、Wintful、Kiro、Cursor 或其他兼容客户端。Agent 应先识别自身所在客户端和其配置方式；无法识别时，只询问用户正在使用哪个客户端，不要求用户自行寻找配置文件。Agent 无法写入客户端配置时，应给出完整配置和明确的放置位置，不能省略本地路径或猜测配置文件。
5. 配置完成后运行 `login --timeout 180`，打开项目专用 Chrome 让用户扫码；不得绕过登录或安全验证。扫码后保持该 Chrome 打开，不要切换账号、手动跳转页面或关闭窗口。登录后验证 MCP 连接，再同步作品列表和最近 20 条作品详情，并汇报安装、登录、同步和数据覆盖情况。
6. 完成安装后必须向用户交接：说明 MCP 已连接、当前登录与数据同步状态；用简明语言说明可查询作品和指标、比较作品表现、分析内容表现、导出数据等功能；给出 3 个可直接发送给 Agent 的首选示例请求。用户未启用视频文案时，明确说明音轨文案分析暂不可用，并提示用户可在需要时说“启用视频文案”。
7. 初次安装不启用视频文案，也不安装 FFmpeg、ASR 模型或其他系统软件；仅当用户明确要求“启用视频文案”时，才说明下载量、磁盘占用和系统改动并征得同意。

> [!NOTE]
> 首次扫码登录、写入 MCP 客户端配置或同步真实数据前，Agent 必须先展示平台风险并取得你的明确确认。

### 备用：手动安装

<details>
<summary><strong>展开查看 PowerShell 安装命令</strong></summary>

```powershell
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
douyin-mcp init
douyin-mcp doctor
```

也可以在已克隆的项目中运行一键脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\easy-install.ps1
```

</details>

<details>
<summary><strong>展开查看 macOS 安装命令</strong></summary>

```bash
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
bash ./easy-install.sh
```

</details>

## 推荐用法

日常使用时，建议先检查缓存新鲜度，再决定是否打开浏览器同步：

```text
检查我的抖音数据状态。只在缓存过期时更新作品列表和最近 20 条详情；
然后按最近 30 天比较完播率、5 秒完播率和互动率，给出复盘结论。
每条结论都说明数据时间、覆盖率、缺失项和对应作品证据。
```

也可以直接提出具体问题：

- “找出最近 30 天互动率最高的 5 条作品，并说明共同点。”
- “对比这 3 条视频的完播率、收藏率和涨粉表现。”
- “结合这 3 条视频的音轨文案和表现数据，对比选题、开头钩子、内容结构、行动价值与互动差异；缺少文案时自动补齐。”
- “提取这条视频的教程步骤和关键结论，并标注对应时间段。”
- “哪些作品值得做续集？说明排序依据和数据局限。”
- “导出全部历史快照为 JSON。”

示例如下：

- 分析作品

  ![](assets/analyze.png)



- 更新缓存

![update](assets/update.png)

## 核心能力

### 获取真实可见的数据

- 首次使用或登录失效时打开可见 Chrome，由用户扫码或完成安全验证。
- 后续复用项目专用浏览器 profile，通常不需要重复登录。
- 增量读取虚拟滚动作品列表，保存播放、点赞、评论、分享和收藏等页面可见指标。
- 按需分批读取作品详情，采集完播率、5 秒完播率、平均观看时长、曝光和涨粉等页面可见指标。

### 查询、对比与复盘

- 查询作品列表、单条作品表现和历史快照。
- 对比 2～20 条作品的关键指标。
- 从视频音轨文案中识别选题、内容结构、关键观点、教程步骤和行动价值。
- 分析或对比所需文案尚未入库时，自动创建后台任务，完成后继续分析。
- 计算点赞率、收藏率、评论率、分享率、播放率和互动率。
- 使用透明、带版本的规则进行轻量潜力排序。
- 生成带数据时间、覆盖率、缺失项和证据引用的复盘上下文。
- 导出 JSON 或 CSV，便于进一步分析或备份。

### 判断结论是否可信

- 返回缓存新鲜度、字段覆盖率、缺失原因和质量警告。
- 页面未显示的值保存为 `null`，不会用 0 或猜测值填充。
- 列表与详情分别保存为快照，不会混写数据来源。
- 派生比率只使用同一原始快照中的分子和分母，并记录公式版本。
- 首次成功同步后绑定当前账号，检测到误切账号时拒绝写入。

## 视频文案提取：用到时再加载

这里的“视频文案”指**视频音轨中实际说出的内容**，不是作品标题、发布描述或画面 OCR。MCP 获取用户本人账号中可访问的公开视频媒体，在本地提取音轨并通过本地 ASR 模型生成原始文本和时间戳分段。

### 启用视频文案

需要时只要对 Agent 说：

```text
启用视频文案，并告诉我需要下载什么、会占用多少磁盘空间和会对系统做哪些改动；得到我的确认后再安装。
```

Agent 应检查或安装 FFmpeg、FFprobe 和 `faster-whisper` 可选依赖，准备本地兼容模型目录，更新项目 `.env`，重新生成 MCP 配置并验证文案能力。模型和运行时不会在普通安装或业务请求中被隐式下载。

### 为什么不在首次启动时提取全部视频

作品元数据通常可以较快同步，而文案提取需要逐条获取媒体并执行本地语音识别。首次启动就处理全部历史视频，会明显延长等待时间并占用更多 CPU、磁盘和浏览器资源。因此启用文案能力后，默认使用混合策略：

| 使用场景 | 默认行为 | 用户感知 |
|---|---|---|
| 首次成功同步 | 作品和指标先入库，后台预热最近 5 条缺少文案的公开视频 | 可以立即查询数据，无需等待全部历史视频 |
| 后续发现新公开视频 | 每次最多自动排队 20 条新视频 | 新内容逐步具备文案上下文 |
| 分析尚无文案的视频 | 自动创建按需任务并返回 `run_id` | Agent 等待任务完成后继续分析 |
| 查询已有文案的视频 | 直接复用当前 revision | 不重复获取或转写 |
| 处理全部历史视频 | 先估算数量、耗时和存储，用户明确确认后执行 | 避免意外启动长任务 |

### 用户如何使用

通常不需要记忆工具名称，直接向 Agent 描述目标即可：

```text
提取这条视频的完整音轨文案，保留时间戳分段；如果尚未入库，
创建后台任务并持续查询，完成后把完整内容展示给我。
```

```text
结合这 3 条视频的音轨文案和表现数据，对比选题、开头钩子、
内容结构、关键观点、行动价值与互动差异；缺少文案时自动补齐。
```

```text
先估算提取全部历史公开视频文案需要的数量、时间和存储空间，
不要立即执行，等我确认后再开始。
```

按需任务会先返回 `run_id`。Agent 可查询任务直到出现以下结果：

- `analysis_ready`：文案已入库，可以读取完整文本、时间戳分段和分析上下文。
- `no_speech`：任务成功，但音轨中没有检测到可用语音。
- `failed`：媒体获取、环境或 ASR 处理失败；应先查看错误原因，再决定是否重试。

### 使用边界

- 只处理当前账号中可访问并被识别为公开视频的作品，不绕过登录、权限或平台验证。
- 原始 ASR 文本可能存在专有名词、英文缩写和同音词误识别；分析时保留原文，展示前可另做纠错和分段。
- 文案提取关注音轨语音，不分析画面内容，也不保证识别背景音乐、无声字幕或画面文字。
- 分析过程中不需要持续播放完整视频；媒体获取完成后，音轨处理和 ASR 在本地后台执行。
- 默认单 worker 处理，优先保证稳定性；不要通过提高并发绕过平台风控或本机资源限制。

## 高级参考

<details>
<summary><strong>查看 CLI、MCP 工具与文案运行方式</strong></summary>

### CLI

以下命令中的 `douyin-mcp` 是简写：macOS 请使用 `./.venv/bin/douyin-mcp`，Windows 请使用 `.\.venv\Scripts\douyin-mcp.exe`。也可以先激活对应的 `.venv`，再直接运行 `douyin-mcp`。

常用命令：

```text
# 首次登录或登录失效
douyin-mcp login --timeout 180

# 同步作品列表
douyin-mcp sync

# 查看作品并取得 video_id
douyin-mcp videos --limit 20

# 每批最多处理 10 条；根据 next_cursor 继续
douyin-mcp details --recent-limit 20
douyin-mcp details --recent-limit 20 --cursor 10

# 查询单条作品表现
douyin-mcp performance <video_id> --period 30d

# 查看登录、缓存、任务和覆盖率
douyin-mcp status
```

<details>
<summary><strong>查看完整 CLI 命令表</strong></summary>

| 命令 | 用途 |
|---|---|
| `init` | 初始化目录和数据库，输出 MCP 配置 |
| `doctor` | 检查运行环境，不打开浏览器 |
| `acknowledge-platform-risk` | 确认已阅读并理解平台自动化访问风险 |
| `login` | 打开浏览器并等待登录 |
| `status` | 查看登录、缓存、同步任务和覆盖率 |
| `sync` | 同步作品列表和列表页指标 |
| `details` | 分批同步指定或近期作品详情 |
| `videos` | 分页查询本地作品 |
| `performance` | 查询单条作品快照和派生指标 |
| `export` | 导出 JSON 或 CSV |
| `purge` | 清除本地数据和专用浏览器 profile |

</details>

### MCP 工具

默认入口保留原有 13 个浏览器数据工具，并新增 9 个本地视频文案流水线工具。

<details open>
<summary><strong>查看 MCP 工具列表</strong></summary>

| 工具 | 用途 |
|---|---|
| `douyin_browser_login_start` | 打开可见 Chrome，处理首次登录或重新登录 |
| `douyin_browser_login_status` | 查询当前浏览器登录状态 |
| `douyin_browser_get_status` | 查询新鲜度、任务、覆盖率、账号绑定和 profile 锁 |
| `douyin_browser_sync_if_needed` | 按 TTL 同步列表、详情或全部数据 |
| `douyin_browser_sync_creator_data` | 同步作品列表和列表指标 |
| `douyin_browser_sync_video_details` | 分批同步指定或近期作品详情指标 |
| `douyin_browser_list_videos` | 分页查询作品和最新指标 |
| `douyin_browser_get_video_comments` | 采集指定/近期作品可见评论与商单意向 insights（v1，不落库） |
| `douyin_browser_get_video_performance` | 查询单作品快照和派生指标 |
| `douyin_browser_compare_videos` | 对比 2～20 条作品 |
| `douyin_browser_get_metric_coverage` | 查询字段覆盖率和缺失原因 |
| `douyin_browser_rank_video_potential` | 使用透明、带版本的规则进行轻量排序 |
| `douyin_browser_generate_review` | 生成带证据和警告的复盘上下文 |
| `douyin_browser_export_data` | 导出 JSON 或 CSV |
| `douyin_browser_submit_transcript_run` | 提交指定/近期文案任务；`all_public=true` 时显式回溯全部公开视频 |
| `douyin_browser_get_transcript_run` | 查询逐视频阶段和逐 run 计数 |
| `douyin_browser_list_transcript_runs` | 分页列出历史文案任务 |
| `douyin_browser_cancel_transcript_run` | 取消当前 run 的需求，不误停共享 job |
| `douyin_browser_retry_transcript_run` | 为失败视频创建新的重试 run |
| `douyin_browser_get_transcript_capabilities` | 诊断 FFmpeg、FFprobe、本地模型和功能门禁 |
| `douyin_browser_get_transcript_backfill_plan` | 只读预估全量历史回溯的数量、耗时和存储 |
| `douyin_browser_get_video_transcript` | 按不可变 revision 分页返回原始时间戳分片 |
| `douyin_browser_get_video_analysis_context` | 返回确定性分析段落；默认自动排队补齐缺失文案 |

</details>

所有工具使用内部账号键 `browser-default`，Agent 无需传递账号 ID。常见业务状态包括 `completed`、`partial`、`cache_hit` 和 `user_action_required`。

### 视频评论采集（v1）

`douyin_browser_get_video_comments(video_id, limit=50, cursor=None, keyword=None, recent=None)`
只读取当前可见作品页评论区的 DOM，不调用私有接口、不绕过验证。v1 不落库、不展开二级回复。

流程说明：公开视频页 `/video/<id>` 会 302 到 `/jingxuan?modal_id=<id>`，评论抽屉**默认关闭**，
DOM 中没有评论节点。采集器会先按语义锚点尝试打开评论入口（右侧带数字的「评论」、`aria-label` 含「评论」、
正文精确「评论」tab），再用「回复」/「展开 N 条回复」actions 定位评论项并向上取容器；不使用哈希类名或
`data-e2e` 作为依赖。打不开或打开后没有渲染评论项时返回 `status=parse_failed` +
`error_code=DOM_MISMATCH`，并用 `reason` 区分：

- `video_not_public`：公开页 404（`previous_page=web_video_404_link`）/作品已删除/仅自己可见时返回
  `status=skipped` + `error_code=VIDEO_NOT_PUBLIC` + `retryable=false`；这是「数据源不适用」，
  **不消耗每日 1 次配额**，也不计入失败率、告警或覆盖率缺口（创作者侧评论请到创作者后台查看）；
- `comments_disabled`：页面明确提示「评论已关闭 / 作者已关闭评论」，返回 `error_code=COMMENTS_DISABLED`，
  不按 `completed` + 0 条处理；
- `panel_not_opened`：评论入口未命中或面板未打开；
- `panel_opened_but_no_items`：面板已打开但没有渲染出评论项；
- `selectors_not_matched`：面板/评论项容器未定位（选择器漂移）。

真机探路（需要本机已登录的 `DOUYIN_BROWSER_PROFILE_DIR`）：

```text
PYENV_VERSION=3.11.11 .venv/bin/python scripts/probe_comment_dom.py \
  --source both --video-id recent --recent-limit 2 --headed
```

`--source public` 走公开视频弹层，`--source creator` 走创作者中心作品管理的「评论」入口；
`--profile`、`--out`、`--settle`、`--timeout-ms`、`--headless` 可覆盖默认值。脚本一次只处理一条作品，
遇到验证立即停止并落盘；产物包含整页 HTML、面板 HTML、截图、分步耗时/哈希与结构化 JSON（`signals` 一律
按实际命中填写）。探路产物可能含真实页面内容，只用于本地排障，不得提交仓库。

```jsonc
{
  "status": "completed | partial | aborted_risk_control | user_action_required | parse_failed",
  "error_code": null,
  "video_id": "...",
  "fetched_at": "2026-11-15T12:00:00+08:00",
  "comments": [{"comment_id": "...", "content": "...", "author_nickname": "...",
                "like_count": 3, "reply_count": 0,
                "posted_at": "2026-11-15T09:00:00+08:00",
                "posted_at_precision": "exact | approx | unknown",
                "is_pinned": false, "is_author_reply": false}],
  "total": 1,
  "total_comment_count": 12,
  "sampled": true,
  "completeness": "truncated",
  "rate_limited": false,
  "has_more": true,
  "next_cursor": "offset:50",
  "insights": {"total_comments": 1, "excluded_author_replies": 0,
               "categories": {"求链接": 0, "提问": 0, "负反馈": 0, "正反馈": 0},
               "multi_label": true, "keyword_rule_version": "comment-keywords-v2",
               "category_details": {"求链接": {"count": 0, "matched_terms": []}},
               "top_words": [{"word": "链接", "count": 1}]},
  "reason": null,
  "parser_version": "comment-panel-v2",
  "warnings": []
}
```

字段语义与缺失值：

- `total_comment_count`：平台显示的总评论数；页面没有明确数字时为 `null`，不估算。
- `sampled` / `completeness`：三态。总数已知且返回数少于总数、或被 `limit` 截断 → `true` / `"truncated"`；总数已知且相等、或明确空态 → `false` / `"complete"`；总数不可得且未截断 → `null` / `"unknown"`（完整性未知，绝不能读成“全量”）。
- `rate_limited`：本地 guard（`DATA_DIR/comment_collection_guard.json`，按 video_id + 日期）显示当天已采集时为 `true`；此时仍返回 `status=completed` 的正常结构，不抛错、不再访问评论页。
- `parse_failed` + `error_code=DOM_MISMATCH`：评论区结构未识别，**不等于 0 条评论**；只有页面明确出现「暂无评论」等空态文案才返回 `completed` + `comments=[]`。
- `aborted_risk_control` + `error_code=RISK_CONTROL`：出现验证/风控立即停止，不自动重试。
- `posted_at_precision`：相对时间按 `fetched_at` 倒推并标 `approx`；完整日期时间标 `exact`；无法解析时 `posted_at=null` + `unknown`。
- `video_id="recent"` 时 `total_comment_count` 为 `null`（跨作品求和非平台展示值），每个评论项额外带 `video_id`，并用 `videos` 列出逐作品状态。
- 明确空态（「暂无评论」等）即使页面没有数字总数也返回 `sampled=false` / `completeness="complete"`。
- 昵称保留（评论者公开昵称），并通过 `responses.py` 的字符串脱敏通道处理 header/URL/路径；任何路径都不返回 cookie、header、用户 id、头像 URL 或主页链接。注意：调用方（MCP 客户端/模型服务）会收到评论文本与昵称，请自行确认平台外处理与向第三方提供的合规边界。
- 分类为关键词多标签（`multi_label=true`），词表可用 `DATA_DIR/comment_keywords.json` 覆盖，返回实际生效的 `keyword_rule_version`。
  v2（`comment-keywords-v2`）会先去掉空格与标点再匹配，并新增 `求发/原文/在哪/怎么弄/多少钱/教程/求` 等变体；
  **v1 与 v2 的分类计数不可直接比较**（历史数据请按版本号分段统计）。`categories` 保持原有计数语义，
  另返回 `category_details[分类] = {"count": N, "matched_terms": [...]}` 供离线核对一致率。
- `total_comment_count` 只从面板头部解析（`共 N 条评论` / `全部评论(N)` / `N 条评论`），解析不到保持 `null`；
  **绝不用解析到的条目数冒充总数**。页面/头部声明总数 N>0 但解析到 0 条时返回 `parse_failed` + `DOM_MISMATCH`。
- 导航竞态（`Execution context was destroyed` 等）会**限速重试 1 次**，随机间隔由
  `DOUYIN_COMMENT_NAV_RETRY_MIN_SECONDS` / `DOUYIN_COMMENT_NAV_RETRY_MAX_SECONDS` 控制（默认 2–3 秒）；
  二次失败仍返回 `retryable=true`，风控/验证类错误绝不重试。
- `parser_version=comment-panel-v2` 由 `browser/comments.py` 维护；评论面板 DOM 漂移时用它追踪解析版本。


### 风控冷却与跳过计数

任一采集（评论采集，或列表/详情同步自身撞到验证）返回风控信号后，写入**账号级**冷却状态（`DATA_DIR/risk_cooldown.json`，
单账号一条，不按采集类型分组），默认 **12 小时**，可用 `DOUYIN_RISK_COOLDOWN_HOURS` 配置为
`6` / `12` / `24`（其它值启动即报 configuration_error）。冷却到期自动失效，无需人工清理。

冷却只阻断**评论采集**（列表/详情同步不打开评论页，风险面不同，冷却期内**照常执行**）。
评论采集在冷却期内直接返回，**不启动浏览器、不消耗任何请求**：

```jsonc
{
  "status": "skipped",
  "reason": "risk_control_cooldown",
  "error_code": "RISK_CONTROL_COOLDOWN",
  "retryable": false,
  "cooldown_until": "2026-11-16T00:00:00+08:00",
  "remaining_seconds": 43200
}
```

- `cooldown_until` 为带时区的 ISO8601（Asia/Shanghai），`remaining_seconds` 为整数秒。
- 冷却不影响只读诊断：`login_status`、`get_status`、本地缓存/DB 查询照常可用。
- `douyin-mcp clear-cooldown --yes` 是**唯一**解除方式（人工确认后调用，必须显式 `--yes`）；
  实现中没有自动清除路径。该命令也能清掉手工写入的状态文件。
- `risk_control_cooldown` 计数只统计被冷却拦截的**评论采集**次数；列表/详情同步不会被该冷却拦截。
- `skipped` 不计失败率、覆盖率缺口与告警，但会计数：`get_status` 返回近 7 日滚动计数，例如
  `"skipped_last_7d": {"risk_control_cooldown": 2, "video_not_public": 1}`。
- `get_status.risk_cooldown` 暴露 `state_path`、`schema`、`active`、`blocked_scope`（当前固定 `comments_only`）、
  `cooldown_until`、`remaining_seconds`，
  便于人工按 schema 注入状态做验收（坏文件/缺字段/时间格式错/已过期一律按“无冷却”处理并给 warning，不报错、不放宽）。
- 最终 URL 与请求 URL 不一致且不属于已知 404 形态时，`warnings` 会写入脱敏后的 `final_url`：
  只保留 path 与 query 的键，例如 `final_url=/jingxuan?previous_page`，不写 query 值。


### 文案流水线配置

视频文案能力默认关闭。请先完成[视频文案提取：用到时再加载](#视频文案提取用到时再加载)中的依赖与模型配置，再设置 `TRANSCRIPT_INGESTION_ENABLED=true`。运行时不会联网下载模型。

MCP 提交只创建持久 run 并立即返回；后台 worker 按视频复用全局 job。同一视频已有可用文案时会直接复用，不会重复获取和转写。`analysis_ready` 和 `no_speech` 都是成功终态，标点恢复或语义分段不会阻塞分析。

启用后默认采用混合策略，而不是首次启动就处理全部历史视频：

| 场景 | 默认行为 |
|---|---|
| 首次成功同步 | 元数据同步立即返回；后台只预热最近 5 条缺失文案的公开视频 |
| 后续同步发现新公开视频 | 每次最多自动排队 20 条新视频 |
| Agent 请求尚未入库的视频分析上下文 | 自动创建文案任务并返回 `run_id`，完成后重新读取上下文 |
| 历史视频 | 默认按需处理，不自动全量回溯 |
| 全量历史回溯 | 用户明确要求后调用 `douyin_browser_submit_transcript_run(all_public=true)` |

对应配置为：

```dotenv
TRANSCRIPT_AUTO_WARMUP_ENABLED=true
TRANSCRIPT_WARMUP_RECENT_LIMIT=5
TRANSCRIPT_AUTO_INGEST_NEW_VIDEOS=true
TRANSCRIPT_AUTO_NEW_VIDEO_LIMIT=20
TRANSCRIPT_AUTO_PREPARE_ANALYSIS=true
```

`TRANSCRIPT_INGESTION_ENABLED=false` 时，上述自动策略全部不会启动。命令行
`douyin-mcp sync` 只负责同步作品列表；需要后台预热和自动补齐时，应让保持运行的
MCP Server 调用 `douyin_browser_sync_creator_data` 或
`douyin_browser_sync_if_needed`。用户无需直接调用工具；推荐指令、任务状态和全量回溯方法已在上方视频文案章节说明。

文案分页固定到不可变 revision，游标在进程重启后仍有效；默认只返回标题、时长和
带时间戳分片。签名媒体 URL、Cookie、Authorization、媒体二进制和绝对本地路径
不会进入 MCP 响应或持久错误。

</details>

## 工作原理

浏览器操作、数据结构化和 Agent 推理被分成三个清晰层次：

1. **浏览器层**：Playwright 操作项目专用 Chrome，读取用户在创作者中心页面中真实可见的内容，不能绕过登录、权限或平台验证。
2. **数据层**：本地服务将页面内容规范化为作品、指标快照、同步任务和质量状态，并保存到本机 SQLite。
3. **MCP 层**：FastMCP 暴露同步、查询、对比和复盘工具，Agent 不需要直接操作 Cookie 或理解页面 DOM。

```text
MCP Client / Agent
        │ stdio
        ▼
douyin_creator_mcp.server
        │
        ├── BrowserExecutor ─ Playwright ── 专用 Chrome profile
        ├── TranscriptCoordinator ─ FFmpeg / local ASR
        └── Database ────────────── data/douyin.sqlite
```

列表同步负责发现作品和采集列表页指标，详情同步按批次访问作品详情页。两种来源分别保存为快照，不会互相覆盖。

### 数据可靠性

- 页面显示什么就保存什么；未显示的值为 `null`，不会用 0 或推测值填充。
- 写入详情前校验作品身份；无法确认时拒绝写入。
- 同一批次、同一作品、同一来源只写一个快照；失败同步不会覆盖历史可信快照。
- `period=30d` 等周期按快照采集时间筛选，`all` 表示全部本地历史。
- 部分作品可能被平台标记为暂不支持详情数据，此时返回 `partial`、失败原因和续跑游标。
- 潜力排序在样本少于 10 条时仅供参考，不代表平台官方评分。
- 页面 DOM、字段可见性和风控策略可能变化，使用时应关注覆盖率和质量警告。

### 登录态、账号与并发

- 登录状态保存在 `data/browser-profile/`，再次启动通常不需要重新扫码。
- 首次成功列表同步会基于作品标题和发布时间摘要建立不可逆账号指纹，不保存昵称、原始标题或作品 ID 作为身份信息。
- 检测到账号变化时返回 `account_mismatch` 并拒绝写入。
- 同一 profile 同时只允许一个同步进程；死亡进程留下的锁会在安全确认后自动恢复。
- 确认要更换账号时，运行 `douyin-mcp purge --yes`，然后重新登录和同步。

### 本地数据与隐私

```text
data/
├── browser-profile/    # 专用 Chrome 登录状态
├── douyin.sqlite       # 作品、指标快照和同步任务
├── media/               # 通过校验的本地转写源（不通过 MCP 返回）
├── staging/             # 可恢复阶段的临时文件
├── exports/            # JSON/CSV 导出
├── reports/            # 本地复盘产物
└── logs/
```

以上目录、数据库、备份和浏览器诊断产物均被 `.gitignore` 排除，请勿使用 `git add -f` 提交。

MCP 不会把 Cookie、`localStorage`、`sessionStorage`、验证码或账号密码返回给 Agent。但作品信息以及你主动查询的创作数据会进入 Agent 上下文；如果 Agent 使用云端模型，还应遵守对应模型服务的数据政策。

导出与清除：

```powershell
douyin-mcp export --format json --period all
douyin-mcp export --format csv --period 30d

# 不带 --yes 时只显示确认提示
douyin-mcp purge
douyin-mcp purge --yes
```

> [!CAUTION]
> `purge --yes` 会删除数据库、数据库备份、导出、报告和专用浏览器 profile，此操作不可恢复。

## 常见问题

<details>
<summary><strong>PowerShell 找不到 <code>douyin-mcp</code></strong></summary>

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

或者直接运行：

```powershell
.\.venv\Scripts\douyin-mcp.exe doctor
```

</details>

<details>
<summary><strong>PowerShell 阻止安装脚本</strong></summary>

下面的执行策略只作用于本次命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\easy-install.ps1
```

</details>

<details>
<summary><strong>登录后仍提示需要操作</strong></summary>

保持项目专用 Chrome 打开，完成扫码、验证码或安全验证，再重试同步。不要使用日常 Chrome profile 替换项目专用 profile。

</details>

<details>
<summary><strong>返回 <code>profile_in_use</code></strong></summary>

另一个同步进程仍在使用专用浏览器。等待它结束后重试；如果原进程已经退出，锁会在安全确认后自动恢复。

</details>

<details>
<summary><strong>详情同步返回 <code>partial</code></strong></summary>

查看 `failures`、`coverage` 和 `next_cursor`。常见原因包括作品暂不支持详情、页面未展示某项指标，或当前批次仍需继续。

</details>

<details>
<summary><strong>文案功能显示未启用或环境不可用</strong></summary>

先让 Agent 调用 `douyin_browser_get_transcript_capabilities`。重点检查：

- `TRANSCRIPT_INGESTION_ENABLED` 是否为 `true`
- 是否安装了 `.[asr]` 可选依赖
- `TRANSCRIPT_ASR_MODEL_DIR` 是否指向有效的本地模型目录
- `ffmpeg` 和 `ffprobe` 是否可以执行

修改 `.env` 后应重新运行 `douyin-mcp init`，更新 MCP 客户端配置并新建会话。

</details>

<details>
<summary><strong>分析视频时返回 <code>preparing</code> 或 <code>run_id</code></strong></summary>

这是按需加载的正常状态，表示该视频尚无可用文案，后台任务已经创建。不要反复提交同一视频；让 Agent 使用返回的 `run_id` 查询进度，任务完成后再次读取分析上下文即可。媒体获取结束后，ASR 会在本地后台执行，不需要一直播放视频。

</details>

<details>
<summary><strong>文案任务完成但结果是 <code>no_speech</code></strong></summary>

`no_speech` 是成功终态，表示模型没有在音轨中检测到可用语音。纯音乐、静音、语音过短或被背景声覆盖的视频可能出现该结果。原始 ASR 也可能误识别专有名词和英文缩写；建议保留原文作为证据，在展示或报告阶段再做纠错。

</details>

## 维护者参考

<details>
<summary><strong>展开开发环境、项目结构和验收说明</strong></summary>

### 开发环境

macOS：

```bash
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest
cp .env.example .env
```

Windows：

```powershell
git clone https://github.com/Kuhakucai/douyin-mcp.git
cd douyin-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pip install pytest
Copy-Item .env.example .env
```

### 项目结构

```text
src/douyin_creator_mcp/
├── server.py                 # 无副作用 FastMCP 构造
├── runtime.py                # 实例锁、迁移、executor/worker 生命周期
├── cli.py                    # 用户 CLI
├── config.py                 # 环境配置
├── browser/
│   ├── session.py            # Playwright 与持久化 profile
│   ├── executor.py           # 唯一同步 Playwright 所有者线程
│   ├── commands.py           # 纯值浏览器命令
│   ├── media_observer.py     # 多 representation 收敛
│   ├── extractors.py         # 列表/详情提取和规范化
│   └── profile_lock.py       # 跨进程 profile 锁
├── services/
│   ├── browser_service.py    # 同步、查询、对比、复盘和导出
│   ├── transcript_coordinator.py # 持久后台 job、lease 与恢复
│   ├── transcript_policy.py  # 首次预热、增量入队和分析按需补齐
│   ├── transcript_query.py   # revision 游标与分析上下文
│   └── metrics.py            # 派生指标与排序公式
├── storage/
│   ├── db.py                 # SQLite、迁移与备份
│   ├── transcripts.py        # run/job/asset/revision 事务仓储
│   ├── migrations/           # 有序、带校验和的不可变迁移
│   └── schemas.sql           # 数据表结构
├── content/
│   ├── media.py              # 受控下载、FFprobe 与轨道选择
│   └── asr.py                # FFmpeg 与本地 faster-whisper
└── tools/
    ├── browser_tools.py      # 原 13 个 MCP 工具契约
    └── transcript_tools.py   # 9 个文案 MCP 工具契约

easy-install.ps1              # Windows 一键安装
easy-install.sh               # macOS/Linux 一键安装
```

### 扩展原则

1. 页面读取和 DOM 处理放在 `browser/`。
2. 可测试的业务逻辑放在 `services/`。
3. MCP 工具只做参数声明、服务调用和统一错误响应。
4. 原始数据、派生指标和不同采集来源分开保存。
5. 新字段必须定义缺失语义、数据来源、解析版本和测试样例。
6. 不读取或返回浏览器认证材料，不接入未公开私有接口。

### 基础验证

以下命令可在 macOS 或 Windows 的已激活虚拟环境中运行；未激活时，请按前文的平台对应路径调用 `douyin-mcp`。

```text
python -m compileall -q src
python -m pip check
douyin-mcp doctor
```

### 真实浏览器验收

涉及 DOM、指标提取或浏览器生命周期的变更，还应使用测试账号运行：

```text
douyin-mcp doctor
douyin-mcp login --timeout 180
douyin-mcp sync
douyin-mcp details --recent-limit 5
douyin-mcp status
```

验收时应核对登录态复用、页面声明数量、加载数量、解析数量、重复同步幂等性、详情身份校验、覆盖率和失败原因。真实账号数据和验收产物不得提交到仓库。

</details>

## 安全、合规与许可

`douyin-mcp` 是独立维护的第三方开源项目，不是抖音、字节跳动或其关联公司的官方、授权、认证或合作产品。

抖音用户服务协议第 2.4、5.1、5.3 和 7.1 条涉及非商业许可、自动化访问、平台外处理或展示信息、向第三方提供信息以及账号处置风险。用户必须自行确认拥有合法账号、数据访问权，以及自动化访问、平台外处理或展示、向 Agent 或模型服务提供数据所需的全部书面授权。完整说明见[平台合规与非官方声明](PLATFORM_COMPLIANCE.md)。

本项目不会通过 MCP 返回 Cookie、`localStorage`、`sessionStorage`、验证码或账号密码，但作品信息和经营数据可能进入 MCP 客户端及 Agent 上下文。“本机运行”不代表业务数据一定不会离开本机。

项目基于 [GNU Affero General Public License v3.0](LICENSE)（`AGPL-3.0-only`）开源。AGPL 允许商业使用代码，但修改版分发及网络交互场景需要按许可证提供对应源代码。许可证不授予抖音平台访问权、数据权、商业使用平台或数据的权利，也不授予商标权。

Copyright (C) 2026 Kuhakucai。`Kuhakucai` 与 `Puppetsho` 是同一作者使用的 Git 提交身份，详见 [AUTHORS.md](AUTHORS.md)。

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读：

- [贡献指南](CONTRIBUTING.md)
- [平台合规声明](PLATFORM_COMPLIANCE.md)
- [开源许可证](LICENSE)

<div align="center">
  <p><strong>如果这个项目对你有帮助，欢迎点一个 Star。</strong></p>
  <p>Built for local, evidence-backed creator analytics.</p>
</div>
