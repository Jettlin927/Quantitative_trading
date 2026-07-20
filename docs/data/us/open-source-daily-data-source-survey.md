# 美股日线开源仓库与正式数据源调研

调研日期：2026-07-21（Asia/Shanghai）

## 结论

GitHub 上有多种开源代码可以获取美股日线，但“客户端开源”不等于“行情数据可免费持久化并用于可复现研究”。对 Issue #27 应分成两层选择：

- 零成本实验和交叉校验：首选 `yfinance`，可辅以 `AKShare`；两者都不能单独晋升为研究级 canonical 数据源。
- 正式研究候选：优先做 `Massive` 与 `Nasdaq Data Link / Sharadar` 的小样本采购前验证；其 Python 客户端开源，数据本身是需授权的产品。若成本优先，可把 `EODHD` 作为第三候选。

当前没有发现同时满足“免费、开源、长期稳定、覆盖退市证券、历史 universe、企业行动、明确可持久化研究许可”全部条件的仓库。

## 候选矩阵

| 仓库 | 能否取日线 | 复权/企业行动 | universe 与退市 | 代码许可与数据边界 | 对 #27 的判断 |
| --- | --- | --- | --- | --- | --- |
| [ranaroussi/yfinance](https://github.com/ranaroussi/yfinance) | 支持 Yahoo 日线；`history` 支持 `1d` | `auto_adjust`、`back_adjust`、拆股和分红事件；另有价格修复功能 | 以传入 ticker 为主，不提供可审计的历史 universe 或完整退市主表 | Apache-2.0 只覆盖代码；项目明确说明是非官方 Yahoo API，供研究/教育和个人使用，数据权利受 Yahoo 条款约束 | **免费实验首选**；适合单票预览、fixture 和交叉校验，不作为 canonical 源 |
| [akfamily/akshare](https://github.com/akfamily/akshare) | `stock_us_daily` 可取新浪美股日线 | 提供前复权因子计算，但官方源码备注个别标的复权因子错误 | 当前股票列表不等于历史 universe；官方文档也提示新浪数据未必覆盖完整上市历史 | MIT 只覆盖代码；项目说明数据仅供学术研究，公开网页端点没有研究级 SLA | **辅助校验**；不能承担正式复权与历史成分合同 |
| [FinanceData/FinanceDataReader](https://github.com/FinanceData/FinanceDataReader) | 支持 Yahoo 美股日线和当前 NASDAQ/NYSE/AMEX 列表 | 返回 Yahoo `Adj Close`，未提供独立、可冻结的完整企业行动账本 | README 的美股 listing 是当前列表；明确的 delisting 支持只见于韩国市场 | MIT 只覆盖代码；Yahoo 路径仍继承 Yahoo 数据使用边界 | 相比直接使用 yfinance 没有解决 #27 的核心缺口 |
| [dpguthrie/yahooquery](https://github.com/dpguthrie/yahooquery) | 支持 Yahoo Chart 历史行情 | 可读取 Yahoo 的事件与调整字段 | 不提供历史 universe 或退市主数据 | MIT 只覆盖代码；README 明确称其为非官方 Yahoo Finance API wrapper | 与 yfinance 同源，维护活跃度和研究合同没有明显优势 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 通过 yfinance、FMP、Intrinio、Tiingo 等 provider 获取历史行情 | 能统一不同 provider 的字段，但语义和许可仍由 provider 决定 | 是否支持退市和 PIT universe 取决于所选付费 provider | AGPL-3.0 代码；它是数据集成平台，不是数据授权 | 当前仓库已有同步与适配框架，引入整个平台过重；选定供应商后写薄适配器更简单 |
| [massive-com/client-python](https://github.com/massive-com/client-python) | 官方 Python 客户端支持聚合 K 线；官方日聚合平面文件覆盖全美股 | REST 支持调整/未调整视图，并提供拆股、分红和 ticker 事件端点 | `All Tickers` 支持按日期查询、`active=false` 退市标的和 `delisted_utc`；ticker change 事件目前仍标为 experimental | MIT 只覆盖客户端；市场数据按 Massive 个人/商业套餐授权 | **正式候选 A**；当前个人套餐全历史日聚合为 Advanced（官方页列示 $199/月），仍需确认内部持久化、报告展示和调用额度 |
| [Nasdaq/data-link-python](https://github.com/Nasdaq/data-link-python) + Sharadar | 开源客户端可访问 `SHARADAR/SEP` 日线表和批量导出 | Sharadar 产品包含日线及配套公司数据；具体企业行动字段须在试用账号内冻结 schema | 官方材料强调约 20 年历史并覆盖 active/delisted，适合降低幸存者偏差；精确 PIT 字段仍须试用验证 | 客户端 MIT；Sharadar 是 Nasdaq Data Link Premium 产品，API key 与订阅必需 | **正式候选 B**；研究定位最接近需求，但当前价格和许可需登录后确认，且官方 Python 客户端代码更新频率较低 |
| [EodHistoricalData/EODHD-APIs-Python-Financial-Library](https://github.com/EodHistoricalData/EODHD-APIs-Python-Financial-Library) | 官方库支持历史 EOD 与交易所批量日线 | 有历史分红、拆股和 bulk API | 官方称美国退市标的超过 12,000；是否能重建任意日期 universe 需实测 | MIT 只覆盖客户端；免费层仅约束为少量调用和有限历史，正式数据需付费许可 | **正式候选 C（成本优先）**；采购前必须重点审计 PIT universe、修订策略和许可 |

## 官方证据与风险

### 免费实验源

- yfinance 的[项目说明](https://github.com/ranaroussi/yfinance)和[官方文档](https://ranaroussi.github.io/yfinance/index.html)都明确其为非官方 Yahoo 接口，并提示 Yahoo 数据面向个人使用；[历史行情参数](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html)虽支持日线、自动复权、事件和修复，但不构成历史证券主表。
- AKShare 的[美股文档](https://akshare.akfamily.xyz/data/stock/stock.html)提供新浪日线与前复权因子，同时提示数据覆盖可能不等于完整上市历史。仓库源码还记录了个别美股复权因子异常，因此不能把计算结果直接作为唯一真值。
- FinanceDataReader 与 yahooquery 都主要封装 Yahoo 公共接口，没有额外解决数据许可、退市身份、历史 universe 和上游修订冻结问题。

### 更接近研究级的付费源

- Massive 的[股票 API 总览](https://massive.com/docs/rest/stocks)列出全市场日汇总、拆股、分红和 ticker 参考数据；[All Tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers)明确支持日期参数、active 状态和退市日期；[日聚合平面文件](https://massive.com/docs/flat-files/stocks/day-aggregates)按套餐提供 5 年、10 年或全历史。其 ticker change 端点仍是 experimental，不能在验收前当作稳定身份合同。
- Sharadar 官方介绍称其[美股数据](https://www.sharadar.com/data)覆盖 active 与 delisted 公司、约 20 年历史，并提供配套日线；Nasdaq Data Link 的[数据组织说明](https://docs.data.nasdaq.com/docs/data-organization)将 Sharadar 列为 Premium 表数据。当前无需 API key 的请求会被拒绝，因此必须通过试用或购买账号验证 `SEP`、`TICKERS`、企业行动和批量导出字段。
- EODHD 的[历史 EOD 文档](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)提供 OHLC、调整收盘价和成交量，[Bulk API](https://eodhd.com/financial-apis/bulk-api-eod-splits-dividends)提供交易所级日线、拆股和分红；其[退市覆盖说明](https://eodhd.com/financial-apis-blog/two-new-fields-home-category-and-isdelisted)是供应商自述，仍需用已知退市样本和历史日期做独立验收。

## 推荐决策

1. 若本阶段必须零成本：采用 `yfinance` 作为实验源、`AKShare` 作为异源抽查，继续保留 SAMPLE/实验标识，不关闭 #27 的研究级门禁。
2. 若目标是正式回测：先申请 Massive 与 Sharadar 的试用或报价，用同一组 active、delisted、ticker-change、split、cash-dividend 样本做并行验收；不要先写 schema 再迁就供应商字段。
3. 若预算明显低于前两者：再验证 EODHD，但必须把 PIT universe 与历史修订可复现性设为一票否决项。

采购前的最小验收应包含：指定历史日期可交易 universe、退市证券全历史、ticker 变更连续身份、原始与复权 OHLCV、拆股和现金分红、交易日历、数据可用时间、批量回填、速率限制、上游修订记录，以及内部持久化和只读研究展示许可。通过这些验证并明确起始年份、证券类型和费用后，Issue #27 才具备转为工程实施票的前提。
