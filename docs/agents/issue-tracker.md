# Issue tracker：GitHub

本仓库使用 `Jettlin927/Quantitative_trading` 的 GitHub Issues 管理研究计划、产品决策、Wayfinder 地图和实施任务。一般 Issue 读写优先使用 GitHub connector；原生子 Issue、阻塞关系和本地分支关联使用 `gh` CLI 补足。

## 约定

- 研究计划、研究问题、自动化进度和最终研究结论均可发布到 Issue。
- Issue 中必须区分运行完成与研究有效，不得把脚本成功等同于策略通过。
- Issue、Pull Request 的标题和正文、Commit、标签、报告和自动化评论必须使用中文。
- 新建 GitHub 标签必须使用中文；外部技能使用的英文角色通过映射文件转换。
- Pull Request 不作为需求或分诊入口。
- GitHub Issue 或 Pull Request 的裸编号可能冲突，读取前先确认对象类型。
- 人类叙述使用带链接的 Issue 标题，不用裸编号代替名称。

## 正式研究控制

- 一条策略使用长期策略档案；每个冻结研究计划使用独立研究 Issue。
- 机器计划使用规范化 JSON 与 `plan_sha256`；中文摘要不替代机器合同。
- 只有 GitHub 用户 `Jettlin927` 的精确评论 `批准研究 <plan_sha256>` 构成批准。标签只是自动化投影，不是授权来源。
- 固定停止评论为 `停止研究 <plan_sha256>`。停止保留运行、事件和工件。
- 编辑关键计划字段、改变代码/快照身份或扩大试验范围后，原批准失效并回到 `研究:待批准`。
- 研究 Issue 在终态评价、工件、Issue 评论、API 与前端读回一致后关闭；后续研究使用新的未批准提案。

## 研究状态标签

- `研究:待批准`
- `研究:已批准`
- `研究:运行中`
- `研究:已发布`
- `研究:受阻`

标签只表达用户需要快速看到的粗粒度状态；细粒度运行阶段和事件保存在 PostgreSQL，不把 Issue 标签做成第二套状态机。

## 类型标签

- `类型:策略研究`
- `类型:产品规格`
- `类型:工程任务`
- `类型:数据功能债`
- `类型:运维功能债`

## 常用操作

- 创建：`gh issue create --title "..." --body "..."`
- 读取：`gh issue view <number> --comments`
- 列表：`gh issue list --state open --json number,title,body,labels,assignees`
- 评论：`gh issue comment <number> --body "..."`
- 标签：`gh issue edit <number> --add-label "..."` 或 `--remove-label "..."`
- 关闭：`gh issue close <number> --comment "..."`

## Wayfinder 操作

- 地图标签：`寻路:地图`。
- 决策票标签：`寻路:调研`、`寻路:原型`、`寻路:访谈`、`寻路:任务`。
- 子票优先使用 GitHub Sub-issues；不可用时，在地图任务列表和子票正文中保留双向指针。
- 阻塞关系优先使用 GitHub 原生 Issue Dependencies；不可用时，才回退到子票正文中的 `阻塞于`。
- 领取决策票时，先把票据分配给当前执行者；未分配且无未完成阻塞项的开放子票构成 frontier。
- 解决决策票时，先发布结论评论，再关闭票据，最后把结论摘要和链接追加到地图的“已决策”部分。
- 所有面向用户的叙述以 Issue 标题称呼决策，不使用裸编号代替名称。

当前仓库方向由 [美股优先仓库整改：退役 A 股、个人不可变记录与旧实验链路](https://github.com/Jettlin927/Quantitative_trading/issues/214) 承接。原 [寻路地图：量化研究自动化、研究驾驶舱与新服务器迁移](https://github.com/Jettlin927/Quantitative_trading/issues/3) 及其阶段性子票属于已关闭的历史路线，不得据此恢复 A 股、公共同步 Worker、个人不可变记录或旧美股实验链路。具体策略研究仍须先读 `docs/research/contracts/strategy-evaluation-standard.md` 并使用独立冻结计划 Issue。

## 发布到 Issue tracker

当技能要求“发布到 issue tracker”时，在本仓库创建 GitHub Issue。当技能要求“读取相关 ticket”时，通过 `gh issue view` 读取正文、标签和评论。
