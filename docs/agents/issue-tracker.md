# Issue tracker：GitHub

本仓库使用 `Jettlin927/Quantitative_trading` 的 GitHub Issues 管理产品决策、Wayfinder 地图和实施任务。读写优先使用可用的 GitHub connector；connector 不支持的原生子 Issue、依赖和本地分支关联使用 `gh` CLI。

## 约定

- 产品决定、工程任务、自动化进度和验收结论可发布到 Issue。
- Issue、Pull Request 的标题和正文、Commit、标签、报告和自动化评论必须使用中文。
- 新建 GitHub 标签必须使用中文；外部技能使用的英文角色通过映射文件转换。
- Pull Request 不作为需求或分诊入口。
- GitHub Issue 或 Pull Request 的裸编号可能冲突，读取前先确认对象类型。
- 人类叙述使用带链接的 Issue 标题，不用裸编号代替名称。

## 类型标签

- `类型:产品规格`
- `类型:工程任务`
- `类型:数据功能债`
- `类型:运维功能债`

## Wayfinder 操作

- 地图标签：`寻路:地图`。
- 决策票标签：`寻路:调研`、`寻路:原型`、`寻路:访谈`、`寻路:任务`。
- 子票优先使用 GitHub Sub-issues；不可用时，在地图任务列表和子票正文中保留双向指针。
- 阻塞关系优先使用 GitHub 原生 Issue Dependencies；不可用时，才回退到子票正文中的 `阻塞于`。
- 领取决策票时，先把票据分配给当前执行者；未分配且无未完成阻塞项的开放子票构成 frontier。
- 解决决策票时，先发布结论评论，再关闭票据，最后把结论摘要和链接追加到地图的“已决策”部分。
- 所有面向用户的叙述以 Issue 标题称呼决策，不使用裸编号代替名称。

仓库当前方向必须从开放 Issue、原生依赖和现场代码读取。产品边界以
[ADR 0011](../adr/0011-personal-investment-workbench-without-research.md) 为准，不从已关闭
地图恢复旧数据或研究入口。

## 发布到 Issue tracker

当技能要求“发布到 issue tracker”时，在本仓库创建 GitHub Issue。当技能要求“读取相关 ticket”时，通过 `gh issue view` 读取正文、标签和评论。
