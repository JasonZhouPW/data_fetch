# GitHub Code Harvester

自动拉取 GitHub 高星 Go、Java、Python、JavaScript、C++ 项目，读取 `2025-10-01` 之后的代码提交，并按项目生成 JSONL。

## 运行

完整启动命令示例：

```bash
export GITHUB_TOKEN=ghp_xxx  # 可选，但建议设置以提高 API 限额
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
  --workers 10 \
  --github-token "$GITHUB_TOKEN"
```

第一步会生成仓库清单：

```text
group_repo.csv
```

字段：

```text
group_repo,url,language,star,author,project_type
```

如果 `group_repo.csv` 已存在，脚本会默认直接读取它并跳过 GitHub repo 搜索，避免重复生成清单。需要重新搜集 100 个 repo 时，加：

```bash
--refresh-repo-csv
```

如果要在已有 `group_repo.csv` 后面继续追加 100 个不重复 repo，加：

```bash
--append-repo-csv --target-repos 100
```

追加后脚本会读取完整 CSV 进入处理流程；已存在 `final_data/<group_repo>.jsonl` 的 repo 会自动跳过，所以不需要单独记录“本次新增列表”。

## 启动参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--output-dir` | `final_data` | 最终每个仓库 JSONL 文件的输出目录。 |
| `--work-dir` | `.cache/github_repos` | clone/fetch 仓库的工作目录。 |
| `--commit-work-dir` | `.cache/commit_json` | 每个 commit 中间 JSON 文件的工作目录。 |
| `--repo-csv` | `group_repo.csv` | 仓库清单 CSV 路径。 |
| `--refresh-repo-csv` | 关闭 | 忽略已存在的仓库清单，重新通过 GitHub Search API 搜索并写入 CSV。 |
| `--append-repo-csv` | 关闭 | 在已有 CSV 后追加不重复的新仓库；追加后读取完整 CSV 处理，已生成 JSONL 的仓库会自动跳过。 |
| `--since` | `2025-10-01` | 只读取该日期之后的 commit，格式为 `YYYY-MM-DD`。 |
| `--languages` | `Go Java Python JavaScript C++` | GitHub Search API 的语言列表，空格分隔，例如 `--languages Python TypeScript`。 |
| `--min-stars` | `5000` | 搜索候选仓库的最低 star 数。 |
| `--repos-per-language` | `100` | 每种语言最多选取的候选仓库数量。 |
| `--target-repos` | `100` | 目标仓库总数；当未设置 `--max-repos` 时生效。 |
| `--max-repos` | 不限制 | 最大可处理仓库数；用于搜索或追加时会覆盖 `--target-repos`。 |
| `--workers` | `10` | commit JSON 生成线程数，实际最小值为 1。 |
| `--clone-workers` | `1` | clone/fetch 线程数，实际最小值为 1。 |
| `--github-token` | 环境变量 `GITHUB_TOKEN` | GitHub API token；可通过参数传入，也可使用环境变量。 |
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
- 启动处理前会检查 `final_data/<group_repo>.jsonl` 是否已存在；存在则跳过该 repo，不再 clone。
- 默认会删除该 repo clone 目录和对应中间 commit JSON 目录；需要调试时可加 `--keep-repos`。
- 单个 repo clone/fetch/process 失败不会终止整批任务；失败会追加记录到 `final_data/failed_repos.log`。
- 如果已有 clone 目录 fetch 失败，脚本会删除该目录并重新 clone 一次。

## 小样本测试

```bash
export GITHUB_TOKEN=$(sed -n '1p' tokens.txt)
python3 -m github_code_harvester \
  --repo fatedier/frp \
  --output-dir final_data \
  --repo-csv group_repo.csv \
  --work-dir .cache/test_github_repos \
  --commit-work-dir .cache/test_commit_json \
  --since 2025-10-01 \
  --max-commits 3 \
  --clone-workers 1 \
  --workers 1
```
