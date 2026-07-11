# AI Daily Brief

每日 AI 精选日报 MVP。它不是全量新闻聚合器，而是抓取公开信息源后先评分、过滤、去重，再生成中文 Markdown 日报，并可通过 Server酱推送微信摘要。

## 功能

- 每天北京时间 06:14 通过 GitHub Actions 自动运行。
- 完整版 Markdown 保存到 `digests/YYYY-MM-DD.md`。
- 当天 Markdown 同时上传为 GitHub Actions Artifact。
- 微信只推送摘要版，不推送完整长文。
- API Key、SendKey、Token 全部来自 GitHub Secrets 或环境变量。
- 任一信息源抓取失败都不会让主流程崩溃。
- 同一事件的官方发布、HN 和 Reddit 讨论会合并成一个 story，避免重复占位。
- 对达到热度门槛的 HN / Reddit 条目抽样高质量评论，提炼支持点、吐槽点和争议。
- 支持人工反馈修正后续评分，并输出一个可验证的“今日产品机会”。

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
- `data/repo_history.json`
- `data/selected_history.json`
- `logs/app.log`

没有 `OPENAI_API_KEY` 时会使用规则模板生成日报。没有 `SERVERCHAN_SENDKEY` 时只生成日报，不推送微信，并在日志中提示。

## GitHub Secrets

在 GitHub 仓库中进入 `Settings -> Secrets and variables -> Actions -> New repository secret`，配置：

- `OPENAI_API_KEY`：可选但推荐，用于增强中文总结。
- `SERVERCHAN_SENDKEY`：可选，用于 Server酱微信推送。

不需要手动配置 `GITHUB_TOKEN`，GitHub Actions 会自动提供。

如果要接入 X Recent Search，再增加：

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

系统会按分组批量查询白名单账号的近 24-48 小时内容。未配置 `X_BEARER_TOKEN` 时会跳过 X，并记录 `X_BEARER_TOKEN not configured, skip X fetch`。

白名单也可以保存 `weibo`、`wechat` 和 `website` 账号作为维护清单。程序只会把 `platform: x` 的账号发送给 X API，其他平台不会被误当成 X 账号抓取。

“数字生命卡兹克”已列入中文 AI 产品观察者，包括微博、公众号官网入口和 AIHOT。由于公众号与微博没有稳定、合规的免鉴权内容接口，自动抓取不可用时使用 `sources/chinese_manual.yml` 补充具体文章：

```yaml
items:
  - title: 文章标题
    url: https://文章原始链接
    source: 数字生命卡兹克
    account: 数字生命卡兹克
    platform: wechat
    published_at: 2026-07-12T10:00:00+08:00
    likes: null
    comments: null
    note: 写清楚文章测试了什么、暴露了什么用户问题，以及为什么值得看。
```

没有 `url` 或具体 `note` 的手动条目不会进入候选。公开热度没有拿到时保留为未知，不会编造。

## 如何维护中文源

编辑 `sources/chinese_sources.yml` 增加中文 RSS 源。第一版包含机器之心、量子位、InfoQ AI、AIbase、36氪 AI、少数派 AI。抓取失败不会影响主流程。

小红书不做自动爬取。需要手动补充时编辑 `sources/xhs_manual.yml`：

```yaml
items:
  - title: 示例标题
    url: https://www.xiaohongshu.com/...
    platform: xiaohongshu
    likes: 120
    collects: 30
    comments: 18
    note: 用户集中吐槽某 AI 工具部署复杂，说明低门槛模板有机会。
    added_at: 2026-07-05T08:00:00+08:00
```

没有 `note` 的小红书条目不会进入精选。

## 如何新增关键词

编辑 `sources/keywords.yml`：

- `english`：英文正向关键词。
- `chinese`：中文正向关键词。
- `downrank`：降权关键词。
- `blocked`：直接过滤关键词。

程序会用关键词初筛 RSS、HN、Reddit、GitHub 和 Hugging Face 候选。

## 同事件合并与社区评论

候选在评分后按原始文章 URL 和标题关键词聚类。同一事件优先保留来源质量更高的主条目，其他来源写入 `related_sources`；HN / Reddit 的讨论指标和评论样本写入 `community_discussions`。因此官方发布负责事实，社区来源负责支持点、吐槽点和争议，同一事件不会重复占用多条精选。

评论正文只针对达到门槛的高热条目抓取，并限制每日请求数量。默认配置：

- `SOCIAL_COMMENT_FETCH_LIMIT=6`
- `HN_COMMENT_MIN_POINTS=30`
- `HN_COMMENT_MIN_COMMENTS=10`
- `REDDIT_COMMENT_MIN_UPVOTES=50`
- `REDDIT_COMMENT_MIN_COMMENTS=10`

评论接口失败时只记录日志，候选仍按已有热度指标继续处理。规则模板会明确标注评论抓取不足；启用模型总结时，模型只能基于实际抓到的评论提炼观点。

## 人工反馈闭环

编辑 `sources/feedback.yml` 可以让你的判断影响后续评分：

```yaml
rules:
  - field: source
    match: 数字生命卡兹克
    rating: useful
    adjustment: 8
    note: 优先观察真实产品测试和中文用户需求
  - field: all
    match: 课程推广
    rating: avoid
    note: 不进入精选
```

`field` 支持 `title`、`url`、`source`、`source_type`、`tags` 和 `all`。`useful` 默认加 6 分，`neutral` 不调整，`avoid` 默认减 25 分并直接过滤；所有修正会保存在候选的 `feedback` 和 `score_breakdown.feedback_adjustment` 中，便于追溯。总修正限制在 -30 到 +15，避免人工规则完全掩盖内容质量。

日报会根据当天最强信号输出一个“今日产品机会”，明确观察到的问题、目标用户、最小验证和为什么现在。它是待验证假设，不会把一条新闻直接包装成产品结论。

## GitHub 新信号机制

GitHub 不再只按 `pushed_at` 和总 stars 选项目。候选会保存 `created_at`、`pushed_at`、`updated_at`、stars、forks、issues、topics、language、release、`star_delta_24h`、`star_delta_7d`、`fork_delta_7d` 等字段。

`data/repo_history.json` 会按日期记录仓库指标，用来计算增长：

```json
{
  "owner/repo": {
    "2026-07-05": {
      "stars": 1234,
      "forks": 120,
      "open_issues": 10,
      "pushed_at": "...",
      "created_at": "..."
    }
  }
}
```

GitHub 信号类型包括：

- `new_project`：30 天内创建，并达到基础 stars 或增长要求。
- `fast_growing`：24h / 7d stars 或 7d forks 明显增长。
- `major_release`：近期 release 或 release 文案命中重大版本关键词。
- `mature_reference`：成熟基础设施，每天最多 1 条，且需要额外信号。
- `maintenance_update`：老项目普通维护更新，默认过滤。

老项目如果没有新增增长、近期 release 或外部讨论，分数会被限制到 70 以下，不能进入精选。

为避免连续几天推送同一个仓库或同一条内容，系统还会维护 `data/selected_history.json`。默认最近 14 天内已经进入精选的内容不会再次入选；同一天手动重跑不会被这条历史挡住，方便测试。可通过环境变量调整：

- `SELECTED_HISTORY_DAYS=14`：最近多少天内不重复推送。
- `SELECTED_HISTORY_KEEP_DAYS=90`：历史记录保留多久。
- `GITHUB_MAX_SELECTED=3`：每天最多进入精选的 GitHub 项目数。
- `GITHUB_MATURE_MAX_SELECTED=1`：每天最多进入精选的成熟 GitHub 项目数。

GitHub 搜索也不再按总 stars 排序抓取高 star 老仓库，而是优先看近期创建、近期更新和 release/增长信号。总 stars 只作为辅助证据，不能单独把老项目推入日报。

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
- X 官方账号：92
- X 高质量研究者：85
- X builder：82
- X 产品观察者 / 中文观察者：78
- HN / Reddit 高热讨论：75
- 中文社区：78
- 高质量技术博客：70
- 普通媒体：50
- 营销号 / 搬运号 / 标题党：直接过滤

## 输出目录

- `digests/`：完整版 Markdown 日报。
- `data/latest.json`：当天精选结果和统计。
- `data/raw_candidates.json`：原始候选内容。
- `data/repo_history.json`：GitHub 仓库历史指标，用于计算增量热度。
- `data/selected_history.json`：已入选内容历史，用于跨天去重，避免连续推送同一内容。
- `sources/feedback.yml`：人工偏好规则，持久化影响后续评分与过滤。
- `sources/chinese_manual.yml`：公众号、微博等不稳定平台的手动降级入口。
- `logs/app.log`：运行日志。

## 当前 MVP 边界

- X / 社交平台：使用 X API Recent Search；没有 `X_BEARER_TOKEN` 时跳过，不影响主流程。
- ModelScope：第一版保留占位接口；未配置稳定无鉴权 API 时跳过。
- OpenAI：用于可选总结增强；没有密钥时使用规则模板兜底。
- 热度数据：拿不到就写“未知”，不会编造。
