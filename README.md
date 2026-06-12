# GitHub Code Harvester

自动拉取 GitHub 高星 Go、Java、Python、JavaScript、C++ 项目，读取 `2025-10-01` 之后的代码提交，并按项目生成 JSONL。

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
--append-repo-csv --languages Java Python JavaScript C++ --target-repos 100
```

追加后脚本会读取完整 CSV 进入处理流程；`finished=true` 的 repo 会自动跳过，`finished=false` 的 repo 会继续处理，所以不需要单独记录“本次新增列表”。

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
- 如果已有 clone 目录 fetch 失败，脚本会删除该目录并重新 clone 一次。

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
