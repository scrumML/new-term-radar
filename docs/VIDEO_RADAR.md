# 专属 AI 视频趋势雷达

目标：从 TikTok 搜索样本发现近期 AI 视频玩法，寻找多个作者的作品表现、制作询问和工具词搜索证据，供人工挑词做生成站。基于 scrumML/new-term-radar，保留原 AI 新词命令；视频雷达使用独立配置、数据库和报告，不会给你发邮件。

## 开始使用

Python 3.10+；核心流程仅用标准库，无需安装依赖。在项目目录执行：

```sh
python3 -m radar videos demo --out reports/videos/demo
open reports/videos/demo/index.html
```

演示全部是虚构数据，不是实际扫描结果。demo 使用独立数据库，不会污染真实记录。

真实扫描需要你自己的 TikHub API 密钥。不要把密钥发到聊天、提交 Git 或写进配置：

```sh
export TIKHUB_API_KEY='你的密钥'
# 可选：沿用原项目的 AIsa 密钥，核验工具词的 Google Trends 与搜索结果。
export AISA_API_KEY='你的密钥'
python3 -m radar videos scan --config video-radar.json
open reports/videos/latest/index.html
```

`.env.example` 只说明变量名称；程序不会自动加载 `.env`。没有 TikHub 密钥时，不会伪造扫描结果，会写出 blocked 状态报告并退出 2。仅有 TikHub 密钥也可扫描，搜索核验缺失会标为 partial；可将 research.enabled 改为 false。

## 扫描如何找到新词

1. 搜索六组宽泛玩法词，同时搜索最近七天的观察词和历史种子。
2. 从视频文案的话题标签、明确包含的历史名称以及导入的 terms 提取候选；支持 CamelCase 拆词和 aliases 合并。查询词本身不当作视频归属证据。
3. 对出现次数较多的新候选再次搜索，补充不同作者作品；历史种子始终放到参照区，不冒充新机会。
4. 对新候选交错采样评论，匹配“what app”“how to”“求模板”等制作问题。
5. 核验前几个候选的 `趋势名 ai generator` 工具词的搜索第一页和 Trends 自身近 30 天变化。沿用上游客户端；移除最后两天未稳定的 Trends 数据。
6. 保存每次采样、首次发现日期、人工选择。下一次采样同一视频，间隔至少一小时且计数未下降，才计算真实观测增速。

发现能力的边界：当前自动命名依赖标签，完全没有可识别标签的玩法可能漏掉；可通过导入 terms 补充。搜索只有一页，不是平台全量，也不是“全网最早”。共用标签不证明同款动作、效果或真实复刻关系，需要打开作品观看。

## 看报告和挑词

每次写出本地 HTML、Markdown、JSON、CSV；HTML 可搜索关键词及 picked 决策，原始链接在报告中可直接打开。

```sh
python3 -m radar videos list
python3 -m radar videos pick '准确的候选名称'
python3 -m radar videos reject '不想做的候选名称'
```

选择会持久保存，下次报告显示；picked 优先复查，rejected 不加入自动观察词，但若广泛搜索再次发现仍会如实报告。`reports/videos/latest/keywords.csv` 可用 Excel 打开。扫描不会购买域名、自动建站或替你决定付费意愿。

报告中的分数为核查优先级，六部分共 100 分：近期独立作者 20、高播放作者 30、制作询问 20、近 72 小时作品 15、AI 可实现线索 5、人工确认同款效果 10。

默认“优先看视频”要求七天内至少三个作者、至少两个作者的作品达到十万播放、存在制作询问，以及 AI 实现线索。这个门槛可调，并不代表达到门槛就会赚钱。

同款效果确认只接受人工导入 `template_confirmed: true`，不会从共同音乐或标签自动推断。`ai_fit_confirmed` 可记录人工完成的实现判断。仍需试生成、测成本、看工具竞争并验证付费。缺失的播放量、日期、评论、Trends、DR 和域龄不会补零后当作低竞争证据。Trends 是相对热度，不是搜索量；首次记录时间不是趋势诞生时间。

## 导入真实证据（可完全离线）

JSON 顶层为数组或 `{"videos": [...]}`。一条视频示例（请替换为实际信息）：

```json
{
  "id": "实际数字视频ID",
  "url": "https://www.tiktok.com/@实际作者/video/实际数字视频ID",
  "author": "实际作者",
  "caption": "视频原文与标签",
  "published_at": "2026-10-29T10:00:00Z",
  "observed_at": "2026-10-29T18:00:00Z",
  "views": 120000,
  "terms": ["你确认的玩法名称"],
  "comments": [{"id": "实际评论ID", "text": "What app is this?"}],
  "template_confirmed": true,
  "ai_fit_confirmed": true,
  "source": "manual"
}
```

```sh
python3 -m radar videos scan --offline --import evidence.json --config video-radar.json
```

同一视频按 ID 去重，重复评论按 ID 或同视频文本去重；旧采样不能覆盖新播放量。未知发布日期或未来日期不参与近期评分；超过 24 小时的观测数据不作为当前热度证据。只应导入你真正采集过的数据，不要把模型估计值写进 views。

## 自动扫描

新增 `.github/workflows/video-radar.yml`，沿用原项目 GitHub Actions 模式：

1. 把改动放进自己的 GitHub 仓库。
2. Settings → Secrets and variables → Actions 添加 `TIKHUB_API_KEY` Secret；可选添加 `AISA_API_KEY` Secret。
3. 添加变量 `VIDEO_RADAR_ENABLED=true`，然后到 Actions 手动 Run workflow 测一次。
4. 工作流计划为每天 UTC 06:15 / 18:15；柏林冬季 07:15 / 19:15，夏季 08:15 / 20:15。报告与 SQLite 随 Actions artifact 保留 30 天。

工作流默认关闭，当前未替你开启。缓存不是永久数据库：缓存丢失或过期会丢失历史增速和选择，请保留 artifact 中的 `video-radar.db`，必要时恢复到 state/videos。手动选择应在持有相同数据库的本机运行。

## 费用和覆盖范围

TikHub 是第三方 TikTok 数据 API，不是 TikTok 官方接口；每次扫描默认最多 32 次请求（包括评论）。无自动重试、无登录 Cookie。达到上限、接口失效、响应结构变化会在报告显示缺口。此限制是请求次数，不能保证固定美元预算，实际价格以供应商账户为准。

AIsa 沿用原项目付费搜索和 Trends 客户端；默认最多三个候选，各一次搜索加一次 Trends。报告只汇总接口返回的费用，失败请求费用可能未知。不要把“未返回”当作“免费”。

TikHub API 依据：

- https://docs.tikhub.io/186826113e0 — TikTok App V3 关键词视频搜索，支持 publish_time / region。
- https://docs.tikhub.io/186826110e0 — 视频评论。

上游来源：scrumML/new-term-radar，读取快照 `3cf6515c4076cb2800819473062d9b36d563d0fe`，保留原 MIT LICENSE。本环境无法 git clone，通过 GitHub 连接读取源码；本地仅同步运行源码、测试和主要文档，远端原有插件 skills/媒体资源不在本地副本中，PR 不删除它们。

## 验证

```sh
python3 -m unittest discover -s tests -p 'test_video*.py' -v
python3 -m radar videos demo --out reports/videos/demo
```

测试覆盖证据门槛、重复作品/评论、旧数据与未来日期、种子隔离、历史增速、人工选词、响应结构变化、HTML 转义、CSV 公式防护与命令行。真实 TikHub/AIsa 端到端采集需配置有效密钥后运行；离线测试不能证明供应商当前可用。
