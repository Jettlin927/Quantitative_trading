# 尾盘 14:30 人工复核 Worklist

- Run: `002-tail-active-minute-manual-worklist-latest-base-001`
- Source run: `002-tail-active-minute-base-latest-dryrun-001`
- Target time: `14:30:00`
- Items: `20`

## Purpose

当前自动分钟源未通过全量回测准入。本 worklist 固定一组候选样本，用于人工或半自动核对 `14:30` 入场价，避免临时挑样本。

## Manual Fields

- `manualMatchedTime`: 实际查到的分钟时间。
- `manual1430Price`: 14:30 或 14:30 前最近一分钟价格。
- `manualSource`: 使用的数据页面或数据源。
- `manualCheckedAt`: 复核时间。
- `manualNotes`: 异常说明，例如停牌、无分钟线、页面不支持历史分钟。

## Promotion Rule

人工复核只能作为数据源可用性证据，不能替代全量分钟回测。若复核样本显示稳定可取价，再回到 `sync_tail_minute_bars.py` 扩展 provider 并输出 `canPromoteToBacktest=true` 后，才能进入分钟级收益验证。

## Items

| # | Date | Code | Name | Industry | Daily close | Next close | Daily next return | Eastmoney | Tencent |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| 1 | 2026-06-03 | 603890.SH | 春秋电子 | 元器件 | 31.33 | 29.36 | -6.29% | [EM](https://quote.eastmoney.com/sh603890.html) | [QQ](https://gu.qq.com/sh603890) |
| 2 | 2026-06-03 | 600459.SH | 贵研铂业 | 小金属 | 24.2 | 24.24 | 0.17% | [EM](https://quote.eastmoney.com/sh600459.html) | [QQ](https://gu.qq.com/sh600459) |
| 3 | 2026-06-03 | 600280.SH | 中央商场 | 百货 | 3.99 | 3.59 | -10.03% | [EM](https://quote.eastmoney.com/sh600280.html) | [QQ](https://gu.qq.com/sh600280) |
| 4 | 2026-06-03 | 002522.SZ | 浙江众成 | 塑料 | 6.7 | 7.1 | 5.97% | [EM](https://quote.eastmoney.com/sz002522.html) | [QQ](https://gu.qq.com/sz002522) |
| 5 | 2026-06-02 | 603007.SH | 顺景科技 | 建筑工程 | 6.21 | 6.16 | -0.81% | [EM](https://quote.eastmoney.com/sh603007.html) | [QQ](https://gu.qq.com/sh603007) |
| 6 | 2026-06-02 | 002995.SZ | 天地在线 | 互联网 | 26.0 | 25.03 | -3.73% | [EM](https://quote.eastmoney.com/sz002995.html) | [QQ](https://gu.qq.com/sz002995) |
| 7 | 2026-06-02 | 002871.SZ | 伟隆股份 | 机械基件 | 22.81 | 22.57 | -1.05% | [EM](https://quote.eastmoney.com/sz002871.html) | [QQ](https://gu.qq.com/sz002871) |
| 8 | 2026-06-02 | 002173.SZ | 创新医疗 | 医疗保健 | 17.79 | 17.96 | 0.96% | [EM](https://quote.eastmoney.com/sz002173.html) | [QQ](https://gu.qq.com/sz002173) |
| 9 | 2026-05-29 | 301565.SZ | 中仑新材 | 塑料 | 29.02 | 26.55 | -8.51% | [EM](https://quote.eastmoney.com/sz301565.html) | [QQ](https://gu.qq.com/sz301565) |
| 10 | 2026-05-29 | 002639.SZ | 雪人集团 | 专用机械 | 17.18 | 15.73 | -8.44% | [EM](https://quote.eastmoney.com/sz002639.html) | [QQ](https://gu.qq.com/sz002639) |
| 11 | 2026-05-29 | 000720.SZ | 新能泰山 | 电气设备 | 5.28 | 5.41 | 2.46% | [EM](https://quote.eastmoney.com/sz000720.html) | [QQ](https://gu.qq.com/sz000720) |
| 12 | 2026-05-27 | 600173.SH | 卧龙新能 | 全国地产 | 7.6 | 7.28 | -4.21% | [EM](https://quote.eastmoney.com/sh600173.html) | [QQ](https://gu.qq.com/sh600173) |
| 13 | 2026-05-26 | 603990.SH | 麦迪科技 | 软件服务 | 19.45 | 19.59 | 0.72% | [EM](https://quote.eastmoney.com/sh603990.html) | [QQ](https://gu.qq.com/sh603990) |
| 14 | 2026-05-26 | 603661.SH | 恒林股份 | 家居用品 | 37.47 | 33.72 | -10.01% | [EM](https://quote.eastmoney.com/sh603661.html) | [QQ](https://gu.qq.com/sh603661) |
| 15 | 2026-05-26 | 600592.SH | 龙溪股份 | 机械基件 | 21.98 | 21.18 | -3.64% | [EM](https://quote.eastmoney.com/sh600592.html) | [QQ](https://gu.qq.com/sh600592) |
| 16 | 2026-05-26 | 300196.SZ | 长海股份 | 玻璃 | 24.75 | 23.93 | -3.31% | [EM](https://quote.eastmoney.com/sz300196.html) | [QQ](https://gu.qq.com/sz300196) |
| 17 | 2026-05-26 | 002056.SZ | 横店东磁 | 电气设备 | 23.45 | 24.52 | 4.56% | [EM](https://quote.eastmoney.com/sz002056.html) | [QQ](https://gu.qq.com/sz002056) |
| 18 | 2026-05-26 | 000926.SZ | 福星股份 | 区域地产 | 2.37 | 2.28 | -3.80% | [EM](https://quote.eastmoney.com/sz000926.html) | [QQ](https://gu.qq.com/sz000926) |
| 19 | 2026-05-25 | 603890.SH | 春秋电子 | 元器件 | 23.84 | 24.27 | 1.80% | [EM](https://quote.eastmoney.com/sh603890.html) | [QQ](https://gu.qq.com/sh603890) |
| 20 | 2026-05-25 | 603678.SH | 火炬电子 | 元器件 | 39.5 | 40.64 | 2.89% | [EM](https://quote.eastmoney.com/sh603678.html) | [QQ](https://gu.qq.com/sh603678) |
