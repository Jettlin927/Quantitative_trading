# GitHub 研究计划与服务端编排器

本模块把 GitHub 研究 Issue 作为控制面，把 PostgreSQL 队列、研究运行与 canonical 工件作为执行面。它只覆盖计划冻结、授权校验、租约队列和单批次运行；不会自动合并代码、执行生产 migration、部署服务器或发布研究结论。

## 不混用的四类状态

| 对象 | 状态 | 含义 |
| --- | --- | --- |
| GitHub 粗粒度投影 | `研究:待批准`、`研究:已批准`、`研究:运行中`、`研究:已发布`、`研究:受阻` | 方便用户浏览；标签不是授权来源 |
| 研究编排 | 待批准、已批准、已排队、运行中、停止中、发布中、已发布、已停止、受阻 | PostgreSQL 中一次冻结计划的工作流状态 |
| Work item / 运行尝试 | 排队、已租用、运行中、成功、失败、中断 | 执行事实；成功不等于研究通过 |
| 研究结论 | 研究通过、有条件候选、证据不足、受阻、不通过 | 后续评价的强制判断 |

研究运行完成后，编排仍保持“运行中/等待评价”，由后续评价与发布模块进入“发布中”和“已发布”，不能把一次运行成功提前映射为研究结论。

## 机器计划合同

Issue 模板位于 [正式研究计划](../../.github/ISSUE_TEMPLATE/正式研究计划.md)。正文必须且只能包含一组：

````text
<!-- research-plan-json:start -->
```json
{ ... }
```
<!-- research-plan-json:end -->
````

当前机器计划 schema 为 `research-plan/v2`。编排器拒绝未知字段、JSON 浮点数、未排序或重复的集合、非 ISO 日期、非字符串十进制定点、缺失 train/validation/test_oos、未启用 walk-forward、非下一交易日执行、非 point-in-time 数据、非单一冻结批次和超服务器上限的预算。`reportContract` 必须冻结市场环境分类与成本压力评价策略，并列出模板中的账本、全周期/OOS 指标、匹配基准、walk-forward 和风险 canonical 工件。规范化 JSON 使用 UTF-8、稳定键序和紧凑分隔符计算 SHA-256；任一 OOS 边界或评价策略变化都会形成新哈希并要求重新批准。

初期 `RESEARCH_MAX_TRIALS=1`，即每张冻结计划只执行一个明确批次。扩大试验预算必须修改服务器上限并形成新计划哈希，不能在同一 OOS 上临时扩张搜索空间。

提交前只读校验保存后的 Issue Markdown：

```bash
python -m backend.app.research_plan /path/to/issue-body.md
```

命令只解析本地文件并打印 `plan_sha256`、批准评论和停止评论，不连接 GitHub、不写数据库、不启动研究。

## 授权、失效、停止与恢复

- 只有 GitHub 用户 `Jettlin927` 的精确评论 `批准研究 <plan_sha256>` 构成授权；首尾空白、附加说明、其他作者或旧哈希都无效。已入队后原批准评论若被编辑或删除，编排器同样阻止新阶段并保留失效事件。
- 正式研究会持久绑定首次被接受的批准评论 ID；后续重复粘贴批准文本不能覆盖更早的停止评论，也不能替代已删除或编辑的原批准。
- 编排器在创建队列前会写回或原样更新授权确认评论，以证明当前 token 仍有 Issue 写权限。权限被拒绝时不创建正式研究、不入队。
- 冻结计划的 `strategy.codeCommit` 必须同时等于镜像内 `APP_GIT_COMMIT`。标准部署脚本只有在工作树无已跟踪或未跟踪改动、且该提交是本地 `origin/main` 的祖先时才注入 `APP_GIT_REF=refs/heads/main`，否则写入 `refs/unverified`；研究 Worker 也不挂载宿主源码。功能分支、脏工作树或无法证明来源的镜像即使含有已登记策略也不得启动正式研究。
- Issue 正文一旦在服务端首次冻结后被编辑，同一计划哈希也永久失效；这是无法从轮询快照安全证明“编辑早于还是晚于批准”时的保守门禁。即使编辑中的 JSON 暂时无效，也会先使既有队列受阻。尚未开始的 work item 中断，运行中的 work item 在下一阶段安全点停止。旧批准、事件、checkpoint 和工件不删除；再次启动必须形成有效的新计划哈希并重新批准。
- 精确评论 `停止研究 <plan_sha256>` 阻止新阶段。中断运行只有在计划、代码、快照和环境身份保持一致且重试预算未耗尽时，才能用 `恢复研究 <plan_sha256> <run_id>` 显式恢复。
- GitHub 读取或写入不可用时，本轮 `github_available=false`，Worker 不领取任何新研究。既有不可变工件仍可由离线复现命令读取。

自动化评论示例：

```text
机器计划已规范化冻结，计划哈希：<plan_sha256>。
当前仍是研究提案，未创建正式研究、未进入服务端队列。
```

```text
已读回授权用户的精确批准评论，并确认当前 GitHub token 具有 Issue 写权限。
服务端仍会校验 Issue 状态、已部署 main 提交、静态策略登记与资源预算。
```

```text
当前计划未进入新研究阶段：<中文受阻原因>。
已有事件、checkpoint 与工件均保留。
```

## GitHub 最小权限

为研究编排器创建独立 fine-grained token，只授予目标仓库：

- Metadata：只读（GitHub 固有）。
- Issues：读写。
- Contents、Pull requests、Actions、Administration：不授予写权限。

客户端代码只暴露列举 Issue、读取评论、写评论和增删研究状态标签四类方法，不存在 push、merge 或仓库设置调用。token 只通过服务器环境变量 `RESEARCH_GITHUB_TOKEN` 注入，不得写入 `.env` 示例、日志、Issue 或仓库。

## PostgreSQL 与 Worker

- `research_orchestrations` 保存每个冻结计划的独立编排状态和被替代关系。
- `research_work_items` 保存租约、Worker、心跳、尝试次数、当前/恢复运行和停止请求。
- PostgreSQL advisory lock 与存活租约共同保证全局最多一个研究 work item；`FOR UPDATE SKIP LOCKED` 防止重复消费。
- 只有数据库 `OperationalError`、超时和连接错误三类可识别基础设施故障按冻结预算最多重试两次；计划违规、数据质量、未知心跳异常、确定性代码错误和资源超限直接受阻。
- 每次新运行生成独立 `attempt_id`，运行必须以该身份登记；每次领取（包括恢复）另生成新的 `lease_token`，防止复用同一 Worker 名称的旧进程越过租约世代续租。租约过期只核对当前 attempt，不能误用同一正式研究的上一运行。仍为 running 的当前运行标成 interrupted，并用相同 `run_id`、`attempt_id` 与 checkpoint 恢复；若当前运行已先持久化为 succeeded 或 failed，则先核对终态并收敛 work item，绝不重复创建运行。所有迁移追加 `research_events`。
- 心跳故障触发安全点中断时，interrupted 运行会写入 `resume_run_id`；若 runner 已持久化终态但收尾心跳失败，work item 保持租约直到到期核对，不重新排队。过期回收使用行锁避开正在续租的存活 Worker，租约或 attempt 已丢失的旧 Worker 不再更新 work item。
- CPU 和内存由 Compose 容器上限约束，墙钟预算在阶段安全点检查，工件预算在运行结束读回。超限保留已有运行与工件，但正式研究进入受阻。

新增 Alembic revision 只提供 schema 代码。生产 `alembic upgrade` 必须另行人工批准，本 Issue 不执行。

## 启用门禁

`research-worker` 位于 `research-automation` Compose profile，普通 `docker compose up` 不会启动。完成代码合并、CI、生产 migration、独立 token 配置和用户上线批准后，才可在目标服务器显式启用该 profile。启用前还必须确认数据 Worker、镜像构建和 migration 不与正式研究并行。

仅做配置解析：

```bash
docker compose --profile research-automation config --quiet
```

这条命令只静默校验配置，不展开环境变量到终端，不创建容器、不迁移数据库、不启动研究。
