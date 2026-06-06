# 尾盘 14:30 人工复核 Worklist

- Run: `002-tail-active-minute-manual-worklist-best-risk-001`
- Source run: `002-tail-active-minute-best-risk-full-dryrun-001`
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
| 1 | 2026-05-26 | 603661.SH | 恒林股份 | 家居用品 | 37.47 | 33.72 | -10.01% | [EM](https://quote.eastmoney.com/sh603661.html) | [QQ](https://gu.qq.com/sh603661) |
| 2 | 2026-05-26 | 600592.SH | 龙溪股份 | 机械基件 | 21.98 | 21.18 | -3.64% | [EM](https://quote.eastmoney.com/sh600592.html) | [QQ](https://gu.qq.com/sh600592) |
| 3 | 2026-05-26 | 002056.SZ | 横店东磁 | 电气设备 | 23.45 | 24.52 | 4.56% | [EM](https://quote.eastmoney.com/sz002056.html) | [QQ](https://gu.qq.com/sz002056) |
| 4 | 2026-05-25 | 603890.SH | 春秋电子 | 元器件 | 23.84 | 24.27 | 1.80% | [EM](https://quote.eastmoney.com/sh603890.html) | [QQ](https://gu.qq.com/sh603890) |
| 5 | 2026-05-25 | 603678.SH | 火炬电子 | 元器件 | 39.5 | 40.64 | 2.89% | [EM](https://quote.eastmoney.com/sh603678.html) | [QQ](https://gu.qq.com/sh603678) |
| 6 | 2026-05-14 | 002409.SZ | 雅克科技 | 半导体 | 106.9 | 107.46 | 0.52% | [EM](https://quote.eastmoney.com/sz002409.html) | [QQ](https://gu.qq.com/sz002409) |
| 7 | 2026-05-12 | 688551.SH | 科威尔 | 专用机械 | 62.4 | 68.18 | 9.26% | [EM](https://quote.eastmoney.com/sh688551.html) | [QQ](https://gu.qq.com/sh688551) |
| 8 | 2026-05-11 | 605566.SH | 福莱蒽特 | 染料涂料 | 35.73 | 34.8 | -2.60% | [EM](https://quote.eastmoney.com/sh605566.html) | [QQ](https://gu.qq.com/sh605566) |
| 9 | 2026-05-11 | 600984.SH | 建设机械 | 工程机械 | 4.88 | 4.82 | -1.23% | [EM](https://quote.eastmoney.com/sh600984.html) | [QQ](https://gu.qq.com/sh600984) |
| 10 | 2026-05-11 | 600391.SH | 航发科技 | 航空 | 44.04 | 42.75 | -2.93% | [EM](https://quote.eastmoney.com/sh600391.html) | [QQ](https://gu.qq.com/sh600391) |
| 11 | 2026-05-11 | 600222.SH | 太龙药业 | 中成药 | 7.74 | 7.45 | -3.75% | [EM](https://quote.eastmoney.com/sh600222.html) | [QQ](https://gu.qq.com/sh600222) |
| 12 | 2026-05-11 | 002997.SZ | 瑞鹄模具 | 汽车配件 | 35.36 | 34.73 | -1.78% | [EM](https://quote.eastmoney.com/sz002997.html) | [QQ](https://gu.qq.com/sz002997) |
| 13 | 2026-05-11 | 002629.SZ | 仁智股份 | 石油开采 | 7.19 | 6.96 | -3.20% | [EM](https://quote.eastmoney.com/sz002629.html) | [QQ](https://gu.qq.com/sz002629) |
| 14 | 2026-05-08 | 603938.SH | 三孚股份 | 化工原料 | 33.97 | 35.53 | 4.59% | [EM](https://quote.eastmoney.com/sh603938.html) | [QQ](https://gu.qq.com/sh603938) |
| 15 | 2026-05-08 | 601868.SH | 中国能建 | 建筑工程 | 3.22 | 3.25 | 0.93% | [EM](https://quote.eastmoney.com/sh601868.html) | [QQ](https://gu.qq.com/sh601868) |
| 16 | 2026-05-08 | 002729.SZ | 好利科技 | 元器件 | 20.85 | 20.62 | -1.10% | [EM](https://quote.eastmoney.com/sz002729.html) | [QQ](https://gu.qq.com/sz002729) |
| 17 | 2026-05-07 | 603269.SH | 海鸥股份 | 机械基件 | 17.06 | 16.87 | -1.11% | [EM](https://quote.eastmoney.com/sh603269.html) | [QQ](https://gu.qq.com/sh603269) |
| 18 | 2026-05-07 | 603162.SH | 海通发展 | 水运 | 17.0 | 18.11 | 6.53% | [EM](https://quote.eastmoney.com/sh603162.html) | [QQ](https://gu.qq.com/sh603162) |
| 19 | 2026-05-07 | 002156.SZ | 通富微电 | 半导体 | 59.08 | 57.16 | -3.25% | [EM](https://quote.eastmoney.com/sz002156.html) | [QQ](https://gu.qq.com/sz002156) |
| 20 | 2026-05-06 | 002354.SZ | 天娱数科 | 互联网 | 6.37 | 6.41 | 0.63% | [EM](https://quote.eastmoney.com/sz002354.html) | [QQ](https://gu.qq.com/sz002354) |
