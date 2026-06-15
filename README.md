# GitHub Code Harvester

自动拉取 GitHub 高星 Go、Java、Python、JavaScript、C++ 项目，读取 `2025-10-01` 之后的代码提交，并按项目生成 JSONL。

## 交付格式与匿名化

所有采集入口输出均为 UTF-8 编码 JSONL，每行一条数据。代码类数据保持如下结构：

```json
{"id":"hash编码","text":"代码内容","meta":{"data_info":{"lang":"编程语言","source":"数据来源","url":"数据来源的网址","type":"代码","author":"作者","public_date":"数据发布时间","project_type":"项目类别"}}}
```

问答、文章、漏洞 patch 等非代码数据也会保留 `meta.data_info` 或等价 meta 信息，包含数据来源、来源 URL、作者、发布时间、类型等可追溯字段。

写出 JSONL 前会自动匿名化可唯一识别个人的信息，命中的内容用小写 `x` 替换，包括邮箱、电话号码、社会安全号码、银行卡/信用卡号，以及带明确标签的出生日期、地址、护照号、驾照号、微信/微博/抖音等个人媒体账号。来源 URL、数据来源、发布时间、许可证等溯源字段会保留，以便满足数据追溯要求。

## 运行

GitHub token 可在项目根目录配置 `token.json`：

```json
{
  "github": "ghp_xxx"
}
```

GitHub token 读取优先级为：命令行参数 > `token.json` > 环境变量。GitLab 默认不读取 `token.json`，公开项目会不带 token 请求；如需 GitLab token，显式传 `--gitlab-token`。

完整启动命令示例：

```bash
python3 -m github_code_harvester \
  --output-dir final_data \
  --work-dir .cache/github_repos \
  --commit-work-dir .cache/commit_json \
  --repo-csv group_repo.csv \
  --since 2025-10-01 \
  --languages Go Java Python JavaScript C++ \
  --min-stars 5000 \
  --repos-per-language 100 \
  --target-repos 100 \
  --max-repos 100 \
  --clone-workers 1 \
  --workers 10
```

第一步会生成仓库清单：

```text
group_repo.csv
```

字段：

```text
group_repo,url,language,star,author,project_type,finished
```

新生成或新追加的 repo 的 `finished` 默认为 `false`。repo 处理成功后会自动回写为 `true`，处理失败则保持 `false`，方便下次继续重跑。

如果 `group_repo.csv` 已存在，脚本会默认直接读取它并跳过 GitHub repo 搜索，避免重复生成清单。此时 `--languages`、`--min-stars`、`--repos-per-language`、`--target-repos` 和 `--max-repos` 不会改变已有 CSV 的语言分布；例如当前 CSV 只有 Go 项目时，即使命令里写了 `--languages Go Java Python JavaScript C++`，仍然只会处理 CSV 里的 Go 项目。

需要按多语言重新搜集 100 个 repo 时，加：

```bash
--refresh-repo-csv --languages Go Java Python JavaScript C++ --target-repos 100
```

如果要在已有 `group_repo.csv` 后面继续追加 100 个不重复 repo，例如补充 Java、Python、JavaScript、C++，加：

```bash
--append-repo-csv --languages Java Python JavaScript C++ --target-repos 100 --search-max-pages 10
```

如果想在满足 `--min-stars` 的候选里按 star 从低到高搜索，加：

```bash
--star-order asc
```

追加后脚本会读取完整 CSV 进入处理流程；`finished=true` 的 repo 会自动跳过，`finished=false` 的 repo 会继续处理，所以不需要单独记录“本次新增列表”。
追加搜索会把 CSV 里已有 repo 当作排重集合；遇到重复会继续翻页查询，尽量直到新增数量达到 `--target-repos`。如果扫描到 `--search-max-pages` 仍不足，会打印 warning。

## 启动参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--output-dir` | `final_data` | 最终每个仓库 JSONL 文件的输出目录。 |
| `--work-dir` | `.cache/github_repos` | clone/fetch 仓库的工作目录。 |
| `--commit-work-dir` | `.cache/commit_json` | 每个 commit 中间 JSON 文件的工作目录。 |
| `--repo-csv` | `group_repo.csv` | 仓库清单 CSV 路径。 |
| `--refresh-repo-csv` | 关闭 | 忽略已存在的仓库清单，重新通过 GitHub Search API 搜索并写入 CSV。 |
| `--append-repo-csv` | 关闭 | 在已有 CSV 后追加不重复的新仓库；追加后读取完整 CSV 处理，`finished=true` 的仓库会自动跳过。 |
| `--since` | `2025-10-01` | 只读取该日期之后的 commit，格式为 `YYYY-MM-DD`。 |
| `--languages` | `Go Java Python JavaScript C++` | GitHub Search API 的语言列表，空格分隔，例如 `--languages Python TypeScript`。 |
| `--min-stars` | `5000` | 搜索候选仓库的最低 star 数。 |
| `--repos-per-language` | `100` | 每种语言最多选取的候选仓库数量。 |
| `--target-repos` | `100` | 目标仓库总数；当未设置 `--max-repos` 时生效。 |
| `--max-repos` | 不限制 | 最大可处理仓库数；用于搜索或追加时会覆盖 `--target-repos`。 |
| `--star-order` | `desc` | GitHub star 排序方向，`desc` 为高到低，`asc` 为低到高。 |
| `--search-max-pages` | `10` | GitHub 搜索每种语言最多向后翻页数；追加 CSV 时用于跳过已有 repo 后继续寻找新 repo。 |
| `--workers` | `10` | commit JSON 生成线程数，实际最小值为 1。 |
| `--clone-workers` | `1` | clone/fetch 线程数，实际最小值为 1。 |
| `--github-token` | `token.json` 的 `github` 字段，或环境变量 `GITHUB_TOKEN` | GitHub API token；命令行参数优先级最高。 |
| `--keep-repos` | 关闭 | 保留 clone 后的仓库目录；默认处理完会删除 clone 目录和中间 commit JSON 目录。 |
| `--repo` | 空 | 处理指定仓库，可重复传入，例如 `--repo fatedier/frp --repo psf/requests`；使用该参数时会按指定仓库生成/覆盖 CSV。 |
| `--max-commits` | 不限制 | 每个仓库最多处理的符合条件 commit 数，适合小样本测试。 |

最终每个仓库生成一个文件，例如：

```text
final_data/owner__repo.jsonl
```

每行格式：

```json
{"id":"commit hash","text":"该 commit 中所有代码文件内容合并后的文本","meta":{"data_info":{"lang":"编程语言","source":"GitHub","url":"本 repo 的 GitHub URL","type":"代码","author":"作者","public_date":"数据发布时间","project_type":"项目类别"}}}
```

## 过滤策略

- 仓库候选来自 GitHub Search API，按语言和 star 数排序。
- 排除名称、描述或 topics 中包含 `tutorial`、`example`、`demo`、`sample`、`synthetic`、`generated`、`llm`、`course` 等明显教学、极简示例或合成数据特征的仓库。
- 仅读取 `--since` 之后的非 merge commit。
- commit 必须包含代码文件，并且代码文件数量不少于非代码文件数量。
- 跳过文档目录、示例目录、依赖锁文件、配置文件和超过 512KB 的单文件。
- 每个符合条件的 commit 输出一条 JSONL 记录，`id` 是该 commit 的 hash。
- `text` 会合并该 commit 中所有通过过滤的代码文件内容，文件之间用一个空行分隔。
- 如果某个 repo 最终没有任何符合条件的记录，不会生成对应的 `final_data/<group_repo>.jsonl`。

## 并发模型

- `--clone-workers` 控制 clone/fetch 线程，默认 1。
- clone 完成一个 repo 后，会立即把该 repo 投递给 commit 生成线程池。
- `--workers` 控制 commit JSON 生成线程池大小，默认 10。
- 每个 commit 会先生成中间文件 `.cache/commit_json/<group_repo>/<commit_hash>.json`。
- 一个 repo 的 commit 都处理完后，中间 JSON 合并为 `final_data/<group_repo>.jsonl`。
- 启动处理前会检查 CSV 中对应 repo 的 `finished` 字段；`true` 则跳过该 repo，不再 clone，`false` 则继续处理。
- repo 处理完成后会把 CSV 中对应记录的 `finished` 字段回写为 `true`；clone/fetch/process 失败则保持 `false`。
- 默认会删除该 repo clone 目录和对应中间 commit JSON 目录；需要调试时可加 `--keep-repos`。
- 单个 repo clone/fetch/process 失败不会终止整批任务；失败会追加记录到 `final_data/failed_repos.log`。
- 正常处理和失败记录重跑都会在 clone repo 之前先调用平台 API 查询默认分支最新 commit 时间；如果最新 commit 早于 `--since`，会跳过 clone，并在 CSV 中把该 repo 标记为 `finished=true`。如果 API 查询失败、返回 429、限流或返回不确定结果，只会打印日志并继续 clone，避免误跳过有效数据或中断任务。
- 如果 shallow clone/fetch 出现 `fatal: error processing shallow info` 这类浅克隆错误，脚本会删除该 repo 缓存目录并自动 fallback 到普通完整 clone。
- 如果已有 clone 目录 fetch 失败，脚本会删除该目录并重新 clone 一次。

## 失败记录重跑

普通 GitHub/GitLab 入口不会根据 `failed_repos.log` 批量重置 CSV 状态。需要专门重跑失败记录时，使用独立入口：

```bash
python3 -m failed_log_harvester \
  --repo-csv group_repo.csv \
  --output-dir final_data_github \
  --work-dir .cache/github_repos \
  --commit-work-dir .cache/commit_json \
  --since 2025-10-01 \
  --clone-workers 1 \
  --workers 10
```

该入口只读取 `failed_repos.log` 中出现、且 CSV 里 `finished=false` 的 repo，并从 CSV 中找到对应记录进行处理；不会简单地把原 CSV 里的记录批量改成 `finished=false`。失败 repo 后续处理成功后，会自动从 `failed_repos.log` 中移除对应历史失败记录。

如果某个 repo 能正常 clone 和扫描，但因为 `--since` 之后没有非 merge commit、没有代码 commit，或符合条件的 commit 最终没有可写入代码内容而生成 0 条 JSONL，该 repo 也会被视为处理完成并回写 `finished=true`。这样即使重启任务，脚本也不会反复处理这类已确认无数据的 repo。

如果失败日志不在 `<output-dir>/failed_repos.log`，可显式指定：

```bash
--failed-log /path/to/failed_repos.log
```

重跑时会打印每个 repo 的诊断日志，用来判断为什么没有生成 JSONL：

```text
Processing 325 repositories with clone_workers=1, workers=10, since=2025-10-01
owner/repo: clone start
owner/repo: clone done at .cache/github_repos/owner__repo
owner/repo: process start
owner/repo: found 12 commits since 2025-10-01
owner/repo: commit scan summary scanned=12 metadata_missing=0 non_code=9 eligible=3 written=0 empty_records=3
owner/repo: wrote 0 records; no final jsonl created
```

如果 clone 前 API 已经确认该 repo 没有 `--since` 之后的默认分支 commit，会看到：

```text
owner/repo: API precheck found no commits since 2025-10-01; skipping clone
```

如果 API 预检查被限流或出错，会继续 clone：

```text
owner/repo: API precheck failed (HTTP Error 429: Too Many Requests); continuing with clone
```

如果 `found 0 commits`，说明该 repo 在 `--since` 之后没有非 merge commit；如果 `non_code` 很高，说明 commit 主要是文档、配置、依赖锁文件或其它非代码文件；如果 `eligible` 大于 0 但 `written=0`/`empty_records` 大于 0，说明 commit 通过了代码文件比例过滤，但实际 checkout 后可读取的代码文件为空、超大或编码不支持。

## 小样本测试

```bash
python3 -m github_code_harvester \
  --repo fatedier/frp \
  --output-dir final_data \
  --repo-csv group_repo.csv \
  --work-dir .cache/test_github_repos \
  --commit-work-dir .cache/test_commit_json \
  --since 2025-10-01 \
  --max-commits 3 \
  --clone-workers 2 \
  --workers 10
```

## GitLab 数据采集

新增 GitLab 入口会复用同一套 clone、commit 过滤和 JSONL 生成逻辑，输出格式与 GitHub 数据一致；区别是 `meta.data_info.source` 会写为 `GitLab`。

GitLab 的过滤要求与 GitHub 保持一致：项目 star 必须严格大于 `--min-stars`，排除 archived 和 mirror 项目，并继续使用名称、描述、topics 中的教学/示例/合成数据关键词过滤；commit 仍然复用同一套非 merge commit、代码文件比例、目录、文件类型和单文件大小过滤。

GitLab 项目列表 API 对大分页请求有时会返回 `HTTP 500`，因此脚本会使用较小的固定页大小请求 GitLab；`--repos-per-language` 仍然表示每种语言最多收集的项目数量，脚本会通过翻页累积到目标数量。

使用 `--append-repo-csv` 时，脚本会从 GitLab 搜索结果中跳过 CSV 里已存在的项目，并继续向后翻页寻找新的项目。GitLab 每页仍固定请求 10 条，以降低 GitLab 500 风险；每种语言最多翻到 `min(100, max(10, --repos-per-language))` 页。如果前面的高星项目都已存在、低于 `--min-stars`、archived/mirror，或被教学/示例过滤，追加数量可能小于目标值。

完整启动命令示例：

```bash
python3 -m gitlab_code_harvester \
  --output-dir final_data_gitlab \
  --work-dir .cache/gitlab_repos \
  --commit-work-dir .cache/gitlab_commit_json \
  --repo-csv gitlab_group_repo.csv \
  --since 2025-10-01 \
  --languages Go Java Python JavaScript C++ \
  --min-stars 5000 \
  --repos-per-language 100 \
  --target-repos 100 \
  --max-repos 100 \
  --clone-workers 1 \
  --workers 10 \
  --gitlab-base-url https://gitlab.com
```

处理指定 GitLab 项目：

```bash
python3 -m gitlab_code_harvester \
  --repo gitlab-org/gitlab \
  --output-dir final_data_gitlab \
  --repo-csv gitlab_group_repo.csv \
  --work-dir .cache/test_gitlab_repos \
  --commit-work-dir .cache/test_gitlab_commit_json \
  --since 2025-10-01 \
  --max-commits 3 \
  --clone-workers 1 \
  --workers 1
```

GitLab 专用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--gitlab-base-url` | `https://gitlab.com` 或环境变量 `GITLAB_BASE_URL` | GitLab 实例地址；自建 GitLab 可改成内网域名。 |
| `--gitlab-token` | 空 | GitLab API token；默认不读取 `token.json`，只有显式传入时才会通过 `PRIVATE-TOKEN` 请求头发送。 |
| `--repo` | 空 | 指定 GitLab 项目路径，可重复传入，例如 `--repo group/project --repo group/subgroup/project`。 |

其他参数与 GitHub 入口含义一致，包括 `--output-dir`、`--work-dir`、`--commit-work-dir`、`--repo-csv`、`--refresh-repo-csv`、`--append-repo-csv`、`--since`、`--languages`、`--min-stars`、`--repos-per-language`、`--target-repos`、`--max-repos`、`--workers`、`--clone-workers`、`--keep-repos` 和 `--max-commits`。

## Gitee 数据采集

Gitee 入口同样复用 clone、commit 过滤和 JSONL 生成逻辑，输出格式与 GitHub/GitLab 数据一致；区别是 `meta.data_info.source` 会写为 `Gitee`。

Gitee token 可在项目根目录配置 `token.json`：

```json
{
  "gitee": "gitee_access_token"
}
```

Gitee token 读取优先级为：命令行参数 > `token.json` > 环境变量 `GITEE_TOKEN`。指定项目处理可以不传 token；批量搜索建议配置 token，因为 Gitee 公开搜索 API 对未授权请求可能返回空列表或受限结果。

使用 `--append-repo-csv` 时，脚本会从 Gitee 搜索结果中跳过 CSV 里已存在的项目，并继续向后翻页寻找新的项目。每种语言最多翻到 `min(100, max(10, --repos-per-language))` 页。如果 Gitee 搜索 API 返回空列表，脚本会自动 fallback 到一组默认公开组织仓库列表继续发现项目：`dromara`、`openeuler`、`mindspore`、`openharmony`、`openkylin`。fallback 仍然只保留公开项目，并继续按 `--min-stars`、语言和教学/示例关键词过滤。

完整启动命令示例：

```bash
python3 -m gitee_code_harvester \
  --append-repo-csv \
  --output-dir final_data_gitee \
  --work-dir .cache/gitee_repos \
  --commit-work-dir .cache/gitee_commit_json \
  --repo-csv gitee_group_repo.csv \
  --since 2025-10-01 \
  --languages Go Java Python JavaScript C++ \
  --min-stars 20 \
  --repos-per-language 2000 \
  --target-repos 1000 \
  --max-repos 1000 \
  --clone-workers 2 \
  --workers 10
```

处理指定 Gitee 项目：

```bash
python3 -m gitee_code_harvester \
  --repo owner/project \
  --output-dir final_data_gitee \
  --repo-csv gitee_group_repo.csv \
  --work-dir .cache/test_gitee_repos \
  --commit-work-dir .cache/test_gitee_commit_json \
  --since 2025-10-01 \
  --max-commits 3 \
  --clone-workers 1 \
  --workers 1
```

Gitee 专用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--gitee-api-base-url` | `https://gitee.com/api/v5` 或环境变量 `GITEE_API_BASE_URL` | Gitee API 地址。 |
| `--gitee-token` | `token.json` 的 `gitee` 字段，或环境变量 `GITEE_TOKEN` | Gitee API access token；命令行参数优先级最高。 |
| `--gitee-seed-org` | 默认内置常见公开组织 | 可选。当 Gitee 搜索 API 返回空列表时，额外扫描指定组织的公开仓库；可重复传入。 |
| `--gitee-seed-user` | 空 | 可选。当 Gitee 搜索 API 返回空列表时，额外扫描指定用户的公开仓库；可重复传入。 |
| `--repo` | 空 | 指定 Gitee 项目路径，可重复传入，例如 `--repo owner/project --repo group/project`。 |

其他参数与 GitHub/GitLab 入口含义一致。

## 漏洞 Patch Pair 数据采集

新增 `vuln_patch_harvester` 入口，用于把已确认来源的 CVE/NVD、厂商公告、CTF、反汇编分析 seed 转换为漏洞修复前后代码对照数据。第一版不做全网漏洞搜索，而是要求输入 curated seed JSONL：每条 seed 明确修复 commit、触发代码和可选汇编注释，脚本负责从 Git 仓库抽取 vulnerable/fixed/diff patch pair。

seed JSONL 示例：

```json
{"id":"CVE-2099-0001","source":"NVD","commit_url":"https://github.com/owner/project/commit/abc123","language":"C","cwe":["CWE-787"],"severity":"HIGH","description":"Buffer overflow in parser.","trigger_code":"parse(payload);","assembly":{"arch":"x86_64","disassembly":"call parse","comments":"触发路径进入未检查边界的 copy 调用"},"license":"MIT","url":"https://nvd.nist.gov/vuln/detail/CVE-2099-0001"}
```

也可以不用 `commit_url`，显式提供：

```json
{"id":"CTF-foo-001","source":"CTF","clone_url":"https://github.com/owner/project.git","html_url":"https://github.com/owner/project","fix_commit":"abc123","trigger_code":"./solve.py","assembly":{"comments":"main+0x42 比较输入长度"}}
```

如果还没有 `vuln_seeds.jsonl`，可以先从 NVD 按日期批量下载 CVE 原始记录，并持续扫描到找到目标数量的修复 commit 候选：

```bash
python3 -m vuln_patch_harvester harvest-nvd-seeds \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --raw-output nvd_raw.jsonl \
  --seed-output vuln_seeds.jsonl \
  --target-seeds 10 \
  --batch-days 30 \
  --records-per-batch 2000
```

`harvest-nvd-seeds` 会调用 NVD 2.0 API 下载 CVE 原始记录，在每个日期窗口内提取 GitHub/GitLab/Gitee commit URL，并生成包含 `needs_enrichment=true` 的候选 seed。NVD/OSV 通常没有触发代码和汇编注释，因此正式生成 patch pair 前，需要人工或另一条分析管线补齐：

- `trigger_code`
- `assembly.disassembly`
- `assembly.comments`

如果只想精确下载指定数量的 NVD 原始记录，再从本地 JSON/JSONL 里抽取 seed，也可以分两步运行：

```bash
python3 -m vuln_patch_harvester fetch-nvd \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --output nvd_raw.jsonl \
  --max-records 1000

python3 -m vuln_patch_harvester build-seeds \
  --input nvd_raw.jsonl \
  --output vuln_seeds.jsonl
```

如有 NVD API key，可通过 `--api-key` 或环境变量 `NVD_API_KEY` 传入。`build-seeds` 会递归读取 `.json`/`.jsonl`，提取 GitHub/GitLab/Gitee commit URL。

正式运行命令：

```bash
python3 -m vuln_patch_harvester \
  --seed-jsonl vuln_seeds.jsonl \
  --output-dir final_data_vuln_patch \
  --work-dir .cache/vuln_patch_repos \
  --require-trigger \
  --max-records 1000
```

输出文件：

```text
final_data_vuln_patch/vuln_patch_pairs.jsonl
```

如果需要转换成安全问答训练格式，类似 `以下代码文件存在何种风险隐患？...`，可以继续运行：

```bash
python3 -m vuln_patch_harvester format-qa \
  --input final_data_vuln_patch/vuln_patch_pairs.jsonl \
  --output final_data_vuln_patch/vuln_patch_qa.jsonl
```

也可以用脚本串起上述流程，默认测试 NVD 2024-01-01 到 2024-01-31，最多输出 10 条 patch/QA。脚本会默认多抓一些 seed 候选，因为不是每个 NVD commit 引用都能抽出代码文件 patch pair：

```bash
./scripts/run_vuln_patch_sample.sh
```

覆盖参数示例：

```bash
./scripts/run_vuln_patch_sample.sh \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --max-records 10 \
  --target-seeds 100
```

如果 seed 已人工补齐 `trigger_code` 和 `assembly.comments`，可开启强制触发代码过滤：

```bash
REQUIRE_TRIGGER=1 ./scripts/run_vuln_patch_sample.sh
```

问答格式每条记录包含：

- `id`
- `text`：漏洞原文代码，按 XML/CDATA 包装。
- `response`：漏洞说明、触发代码、修复后安全代码、patch diff、修复思路、安全规范解释和汇编注释。
- `label`
- `source`
- `meta`

每条记录包含：

- `code.vulnerable`：修复 commit 的父提交中的漏洞代码。
- `code.fixed`：修复 commit 中的修复代码。
- `code.diff`：对应文件的 Git diff。
- `trigger.code`：seed 中提供的漏洞触发代码、PoC、fuzz input 或 CTF 输入。
- `assembly`：seed 中提供的反汇编代码和注释。

注意：脚本不会自动执行 PoC，也不会验证攻击效果；`trigger.safe_to_run` 固定写为 `false`。这类数据建议只使用公开授权来源，并保留 `license` 和 `url`。

## 技术讨论数据采集

新增 `discussion_harvester` 入口，用于采集代码相关的技术问答、技术文章和深度讨论数据。当前支持：

- `stackoverflow`：通过 Stack Exchange 官方 API 采集，许可证清晰。
- `csdn`：采集公开 CSDN 博客文章页面。
- `zhihu`：采集公开知乎回答或知乎专栏页面。

CSDN/知乎入口只处理公开可访问页面，带限速、域名和 URL 形态过滤；遇到登录墙、反爬限制、短正文或非代码内容会跳过。脚本不会绕过登录、验证码、付费墙或站点访问控制。批量使用前请确认你有相应授权，并遵守站点 robots、服务条款和内容版权要求。

Stack Overflow 完整启动命令示例：

```bash
python3 -m discussion_harvester stackoverflow \
  --output-dir final_data_stackoverflow \
  --site stackoverflow \
  --tags python java javascript go c++ \
  --since 2024-01-01 \
  --min-score 10 \
  --min-answers 1 \
  --max-records 50000 \
  --max-answers 3 \
  --page-size 100 \
  --sleep-seconds 0.25
```

如果有 Stack Exchange API key，可在项目根目录 `token.json` 中增加：

```json
{
  "github": "ghp_xxx",
  "stackexchange": "stackexchange_key_xxx"
}
```

也可以通过命令行显式传入：

```bash
--stackexchange-key stackexchange_key_xxx
```

Stack Overflow 参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--output-dir` | `final_data_stackoverflow` | Stack Overflow JSONL 输出目录。 |
| `--site` | `stackoverflow` | Stack Exchange site 名称。 |
| `--tags` | `python java javascript go c++` | 要采集的问题 tags，空格分隔。 |
| `--since` | `2024-01-01` | 只采集该日期之后创建的问题，格式为 `YYYY-MM-DD`。 |
| `--min-score` | `10` | 问题最低 score。 |
| `--min-answers` | `1` | 问题最低 answer 数。 |
| `--max-records` | `10000` | 本次最多写入的问题记录总数，跨所有 tags 计数。 |
| `--max-answers` | `3` | 每个问题最多合并的高赞/accepted answers 数。 |
| `--page-size` | `100` | Stack Exchange API 每页数量，脚本会限制在 1 到 100。 |
| `--stackexchange-key` | `token.json` 的 `stackexchange` 字段，或环境变量 `STACKEXCHANGE_KEY` | 可选 Stack Exchange API key；命令行参数优先级最高。 |
| `--sleep-seconds` | `0.25` | API 翻页之间的等待时间；如果 API 返回 `backoff`，优先按 `backoff` 等待。 |

输出文件按 tag 分开，例如：

```text
final_data_stackoverflow/stackoverflow_python.jsonl
```

每行格式：

```json
{"id":"stackoverflow_question_123","text":"Title + Question + Accepted/high-score answers","meta":{"data_info":{"source":"StackOverflow","type":"技术问答","url":"https://stackoverflow.com/questions/123/...","license":"CC BY-SA 4.0","site":"stackoverflow","tag":"python","tags":["python","json"],"score":42,"views":9000,"answer_count":2,"author":"Question Author","public_date":"2024-01-01T00:00:00+00:00"}}}
```

采集策略：

- 使用 Stack Exchange 官方 API 的 `/search/advanced` 拉取问题正文，再通过 `/questions/{ids}/answers` 拉取答案正文。
- 问题按 votes 排序，筛选 `--since` 之后、score 不低于 `--min-score`、answer 数不低于 `--min-answers` 的记录。
- 每个问题合并 title、question body，以及 accepted answer 或高赞 answers。
- 不同 tag 命中的同一个 question 只写入一次，保留第一次命中的 tag。
- Stack Overflow 内容许可证为 CC BY-SA，输出中保留 `url`、`author`、`license` 和 `public_date`，后续使用时仍需遵守 attribution 和 share-alike 要求。

### Stack Overflow Dump

如果服务器上已有 Stack Exchange dump，生成问答 JSONL 需要 `Posts.xml`。`Badges.xml`、`Comments.xml`、`PostHistory.xml` 不能单独生成完整问答数据。

本地 dump 入口会流式解析 `Posts.xml`，不会一次性把 6GB+ XML 读入内存。处理命令：

```bash
python3 -m discussion_harvester stackoverflow-dump \
  --posts-xml /path/to/Posts.xml \
  --output-dir final_data_stackoverflow_dump \
  --tags python java javascript go c++ \
  --min-score 10 \
  --min-answers 10 \
  --max-answers 5 \
  --records-per-file 500 \
  --progress-interval 100000
```

该入口默认产出“代码分析与解释”类数据：只保留编程语言 tag 命中的 question，并要求 question 正文包含代码块、函数调用、赋值、异常/报错、SQL/函数定义等明显代码分析形态；泛泛的职业建议、语言选择、非代码讨论会被跳过。默认要求 question 的 score 不低于 10、answer 数不少于 10，并合并分数最高的 5 条回答，适合做代码解释/问题分析数据。默认不限制总记录数，会一直顺序处理到 `Posts.xml` 结束；如需测试小样本，可显式传 `--max-records 1000`。

输出文件按 500 条一份递增生成：

```text
final_data_stackoverflow_dump/stackoverflow_dump_000001.jsonl
final_data_stackoverflow_dump/stackoverflow_dump_000002.jsonl
...
```

同时会写 checkpoint：

```text
final_data_stackoverflow_dump/stackoverflow_dump.checkpoint.json
```

如果中间错误退出，下一次运行同一命令会读取 checkpoint，从上一次完整写完的 question id 后继续处理。需要从头重跑时加 `--reset-checkpoint`。

dump 参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--posts-xml` | 必填 | Stack Overflow dump 中的 `Posts.xml` 路径。 |
| `--output-dir` | `final_data_stackoverflow_dump` | dump JSONL 输出目录。 |
| `--output-prefix` | `stackoverflow_dump` | 输出 JSONL 文件名前缀。 |
| `--checkpoint-file` | `<output-dir>/<output-prefix>.checkpoint.json` | 续跑 checkpoint 文件。 |
| `--reset-checkpoint` | 关闭 | 忽略已有 checkpoint，从头开始。 |
| `--tags` | `python java javascript go c++` | 要保留的问题 tags。 |
| `--since` | 空 | 可选日期过滤；默认不按时间过滤，顺序读取 dump。 |
| `--min-score` | `10` | 问题最低 score。 |
| `--min-answers` | `10` | 问题最低 answer 数。 |
| `--max-records` | `0` | 最多写入的问题记录数；`0` 表示不限制，处理到 dump 结束。 |
| `--max-answers` | `5` | 每个问题最多合并的最高赞 answers 数。 |
| `--records-per-file` | `500` | 每个 JSONL 文件的记录数。 |
| `--progress-interval` | `100000` | 每扫描多少行 `Posts.xml` 打印一次进度。 |

实现上会扫描 `Posts.xml` 两遍：第一遍从 checkpoint 记录的 question id 后继续顺序筛出符合条件的代码分析/解释类 question，第二遍只收集这些 question 的最高赞 answers，然后按批次写出 JSONL。每扫描 `--progress-interval` 行会打印当前扫描进度，每写完一个 JSONL 文件就更新 checkpoint。

CSDN 公开文章采集示例：

```bash
python3 -m discussion_harvester csdn \
  --output-dir final_data_csdn \
  --queries python java javascript go c++ 代码 \
  --max-pages 3 \
  --max-records 1000 \
  --min-chars 300 \
  --sleep-seconds 1.0
```

知乎公开回答/专栏采集示例：

```bash
python3 -m discussion_harvester zhihu \
  --output-dir final_data_zhihu \
  --queries python java javascript go c++ 代码 \
  --max-pages 3 \
  --max-records 1000 \
  --min-chars 300 \
  --sleep-seconds 1.0
```

如果已经有授权 URL 清单，建议优先使用显式 URL，稳定性会高于站内搜索页：

```bash
python3 -m discussion_harvester csdn \
  --url-file csdn_urls.txt \
  --queries \
  --output-dir final_data_csdn
```

CSDN/知乎参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--output-dir` | `final_data_csdn` / `final_data_zhihu` | JSONL 输出目录。 |
| `--queries` | `python java javascript go c++ 代码` | 搜索关键词，空格分隔；如果只想处理 `--url`/`--url-file`，可传空的 `--queries`。 |
| `--url` | 空 | 显式公开文章/回答 URL，可重复传入。 |
| `--url-file` | 空 | 每行一个公开 URL 的文本文件。 |
| `--max-pages` | `3` | 每个关键词扫描的搜索结果页数。 |
| `--max-records` | `1000` | 本次最多写入记录数。 |
| `--min-chars` | `300` | 清洗后正文最少字符数，低于该值会跳过。 |
| `--sleep-seconds` | `1.0` | HTTP 请求之间的等待时间。 |

输出文件：

```text
final_data_csdn/csdn_articles.jsonl
final_data_zhihu/zhihu_articles.jsonl
```

每行格式：

```json
{"id":"csdn_blog.csdn.net__user__article__details__123","text":"Title + cleaned public article body","meta":{"data_info":{"source":"CSDN","type":"技术文章","url":"https://blog.csdn.net/user/article/details/123","author":"作者","public_date":"2026-01-01T00:00:00+08:00"}}}
```

采集策略：

- CSDN 仅保留 `blog.csdn.net/.../article/details/...`。
- 知乎仅保留 `zhuanlan.zhihu.com/p/...`、`zhihu.com/question/...` 和 `zhihu.com/question/.../answer/...`。
- 清洗 HTML 时跳过 `script`、`style`、`noscript`、`svg`，并抽取 title、meta author、meta published time。
- 通过代码关键词和函数调用形态做轻量过滤，降低泛娱乐、营销或非代码内容混入。
- 输出中保留来源、URL、作者和发布时间，方便后续去重、授权核查和溯源。
