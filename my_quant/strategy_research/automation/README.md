# B1 次日盘前预案自动化

这个目录保存 B1 趋势回调策略的盘前预案自动化入口。默认在 macOS 上用 `launchd` 每天下午 6 点运行一次，也可以交给 Codex 自动化执行。

## 运行内容

`run_b1_daily.sh` 这个文件名保留给旧定时任务兼容，但它现在生成的是次日盘前预案，不是回测日报。

脚本会：

- 读取仓库根目录的 `.env.local`，但不会打印 `TUSHARE_TOKEN`。
- 用 `.venv/bin/python` 执行 `my_quant.strategy_research.web_report.build_b1_premarket_plan`。
- 默认用当天日期作为信号数据截止日。
- 基于最新收盘信号生成次日盘前动作：
  - 优先卖出 / 风控：已有模型持仓触发跌破 BBI 或 8% 止盈。
  - 候选买入：市场门打开时，按 B1 分数排序后的 Top2 候选。
  - 继续观察：未触发卖出条件的模型持仓。
- 生成带日期的 HTML：`my_quant/strategy_research/web_report/premarket/b1_premarket_plan_YYYYMMDD.html`。
- 复制一份最新预案到：`my_quant/strategy_research/web_report/b1_premarket_plan_latest.html`。
- 写入日志：`my_quant/strategy_research/logs/b1_premarket_runs.log`。

## 手动跑一次

在仓库根目录执行：

```bash
chmod +x my_quant/strategy_research/automation/run_b1_daily.sh
my_quant/strategy_research/automation/run_b1_daily.sh
```

指定信号截止日期做复现：

```bash
B1_END_DATE=2026-06-17 B1_RUN_DATE=20260617 my_quant/strategy_research/automation/run_b1_daily.sh
```

## 安装每天 18:00 自动任务

```bash
mkdir -p my_quant/strategy_research/logs ~/Library/LaunchAgents
chmod +x my_quant/strategy_research/automation/run_b1_daily.sh
cp my_quant/strategy_research/automation/com.jettlin.xquant.b1-daily.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jettlin.xquant.b1-daily.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jettlin.xquant.b1-daily.plist
launchctl enable gui/$(id -u)/com.jettlin.xquant.b1-daily
```

立即试跑一次定时任务：

```bash
launchctl kickstart -k gui/$(id -u)/com.jettlin.xquant.b1-daily
```

查看状态：

```bash
launchctl print gui/$(id -u)/com.jettlin.xquant.b1-daily
```

查看日志：

```bash
tail -f my_quant/strategy_research/logs/b1_premarket.out.log
tail -f my_quant/strategy_research/logs/b1_premarket.err.log
```

## 卸载

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jettlin.xquant.b1-daily.plist
rm ~/Library/LaunchAgents/com.jettlin.xquant.b1-daily.plist
```

## 前置条件

- `.env.local` 中有 `TUSHARE_TOKEN=...`。
- 仓库根目录 `.venv` 已安装 `my_quant/requirements.txt` 中的策略依赖：

```bash
uv venv .venv --python 3.12
. .venv/bin/activate
uv pip install -r my_quant/requirements.txt
```

不用 uv 时可改用：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r my_quant/requirements.txt
```

- 本任务只做研究预案和人工执行辅助，不自动下单。
- 节假日的“次日”默认按工作日估算，实盘前需要人工确认交易日历。
