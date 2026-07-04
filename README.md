# AI Daily Brief

每日 AI 精选日报 MVP。它不是全量新闻聚合器，而是抓取公开信息源后先评分、过滤、去重，再生成中文 Markdown 日报，并可通过 Server酱推送微信摘要。

## 功能

- 每天北京时间 06:14 通过 GitHub Actions 自动运行。
- 完整版 Markdown 保存到 `digests/YYYY-MM-DD.md`。
- 当天 Markdown 同时上传为 GitHub Actions Artifact。
- 微信只推送摘要版，不推送完整长文。
- API Key、SendKey、Token 全部来自 GitHub Secrets 或环境变量。
- 任一信息源抓取失败都不会让主流程崩溃。

## 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/main.py
```

生成指定日期的测试日报，例如 2026-07-03：

```bash
BRIEF_DATE=2026-07-03 python scripts/main.py
```

设置 `BRIEF_DATE` 后，程序会按该日期的北京时间日历窗口过滤候选，避免把未来更新误放进历史日报。

运行后会生成：

- `digests/YYYY-MM-DD.md`
- `data/latest.json`
- `data/raw_candidates.json`
- `logs/app.log`

没有 `OPENAI_API_KEY` 时会使用规则模板生成日报。没有 `SERVERCHAN_SENDKEY` 时只生成日报，不推送微信，并在日志中提示。

## GitHub Secrets

在 GitHub 仓库中进入 `Settings -> Secrets and variables -> Actions -> New repository secret`，配置：

- `OPENAI_API_KEY`：可选但推荐，用于增强中文总结。
- `SERVERCHAN_SENDKEY`：可选，用于 Server酱微信推送。

不需要手动配置 `GITHUB_TOKEN`，GitHub Actions 会自动提供。

后续如果接入 X API，再增加：

- `X_BEARER_TOKEN`

## GitHub Actions

workflow 文件在 `.github/workflows/daily-ai-brief.yml`。

支持两种运行方式：

- 定时运行：`14 22 * * *`
- 手动运行：`workflow_dispatch`

cron 使用 UTC。北京时间 `Asia/Shanghai 06:14 = UTC 22:14 前一天`，所以 workflow 使用：

```yaml
schedule:
  - cron: "14 22 * * *"
```

每次运行后会：

- 生成 `digests/YYYY-MM-DD.md`
- 生成 `data/latest.json`
- 生成 `data/raw_candidates.json`
- 生成 `logs/app.log`
- 自动 commit 到当前仓库
- 上传 Artifact，名称为 `ai-daily-brief-YYYY-MM-DD`

如果当天没有文件变化，workflow 会输出 `No generated file changes to commit.` 并正常结束。

## 如何下载完整版 Markdown

方法一：在仓库的 `digests/` 文件夹里查看。

方法二：进入 GitHub Actions 当天 workflow run，在 `Artifacts` 里下载 `ai-daily-brief-YYYY-MM-DD`。

方法三：本地执行 `git pull` 后查看 `digests/` 文件夹。

## 如何新增 RSS 源

编辑 `sources/rss_sources.yml`：

```yaml
sources:
  - name: Example AI Blog
    url: https://example.com/rss.xml
    source_type: official
    fetch_type: rss
    enabled: true
    timeout: 15
```

没有稳定 RSS 的页面可以先保留为占位：

```yaml
fetch_type: page_placeholder
enabled: false
```

## 如何维护社交账号白名单

编辑 `sources/social_accounts.yml`，按分组增加账号：

- `official_accounts`
- `high_quality_ai_researchers`
- `ai_builders`
- `ai_coding_tool_authors`
- `ai_product_observers`
- `chinese_ai_product_observers`

第一版不会全量爬 X，只加载白名单并记录日志。后续接入 X API 时，使用 `X_BEARER_TOKEN` 扩展抓取器即可。

## 如何新增关键词

编辑 `sources/keywords.yml`：

- `english`：英文正向关键词。
- `chinese`：中文正向关键词。
- `downrank`：降权关键词。
- `blocked`：直接过滤关键词。

程序会用关键词初筛 RSS、HN、Reddit、GitHub 和 Hugging Face 候选。

## 评分规则

每条候选内容满分 100：

```text
score = 来源质量分 * 0.35
      + 热度分 * 0.25
      + 新颖性分 * 0.20
      + 产品启发分 * 0.20
```

默认 `MIN_SCORE=75`，`MAX_ITEMS=8`。可以通过 `.env`、GitHub Actions variables 或环境变量调整。

来源质量默认分：

- 官方源：95
- 白名单高质量账号：85
- HN / Reddit 高热讨论：75
- 高质量技术博客：70
- 普通媒体：50
- 营销号 / 搬运号 / 标题党：直接过滤

## 输出目录

- `digests/`：完整版 Markdown 日报。
- `data/latest.json`：当天精选结果和统计。
- `data/raw_candidates.json`：原始候选内容。
- `logs/app.log`：运行日志。

## 当前 MVP 边界

- X / 社交平台：第一版只保留白名单配置和接口结构，不做网页抓取，不影响主流程。
- ModelScope：第一版保留占位接口；未配置稳定无鉴权 API 时跳过。
- OpenAI：用于可选总结增强；没有密钥时使用规则模板兜底。
- 热度数据：拿不到就写“未知”，不会编造。
