# External High-Volatility Satellite Probe

- Evaluation window: `2021-01-01` to `2026-06-15`.
- Assets: `TQQQ, SOXL, TECL, UPRO, BTC-USD, ETH-USD, GLD, TLT`.
- 50% annual return and -30% max drawdown passing rows: `0`.
- Rows with annual return >= 50% before drawdown gate: `1`.
- Best drawdown-qualified near-miss: `ext_ram_top1_m10_v10_t200_f5_x1_0_GLD`.
- Annual return: `24.14%`.
- Max drawdown: `-27.78%`.

## Top Drawdown-Qualified Rows

| strategy                                | strategy_family   |   annual_return |   max_drawdown |   calmar |   gross_exposure |
|:----------------------------------------|:------------------|----------------:|---------------:|---------:|-----------------:|
| ext_ram_top1_m10_v10_t200_f5_x1_0_GLD   | external_ram      |          0.2414 |        -0.2778 |   0.8691 |           1.0000 |
| ext_ram_top1_m10_v60_t200_f5_x1_0_GLD   | external_ram      |          0.2302 |        -0.2900 |   0.7938 |           1.0000 |
| ext_ram_top1_m10_v20_t200_f5_x1_0_GLD   | external_ram      |          0.2193 |        -0.2900 |   0.7562 |           1.0000 |
| ext_ram_top2_m10_v20_t200_f5_x1_0_GLD   | external_ram      |          0.2152 |        -0.2913 |   0.7385 |           1.0000 |
| ext_ram_top1_m10_v10_t200_f5_x1_0_CASH  | external_ram      |          0.2051 |        -0.2807 |   0.7306 |           1.0000 |
| ext_ram_top1_m10_v20_t200_f5_x1_0_CASH  | external_ram      |          0.1738 |        -0.2803 |   0.6201 |           1.0000 |
| ext_ram_top2_m10_v60_t200_f5_x1_0_CASH  | external_ram      |          0.1737 |        -0.2925 |   0.5938 |           1.0000 |
| ext_ram_top2_m120_v10_t200_f5_x1_0_CASH | external_ram      |          0.1733 |        -0.2904 |   0.5968 |           1.0000 |
| ext_ram_top1_m10_v60_t200_f5_x1_0_CASH  | external_ram      |          0.1590 |        -0.2803 |   0.5673 |           1.0000 |
| ext_ram_top2_m10_v10_t200_f5_x1_0_GLD   | external_ram      |          0.1554 |        -0.2709 |   0.5736 |           1.0000 |

## Top Return Rows

| strategy                               | strategy_family   |   annual_return |   max_drawdown |   calmar |   gross_exposure |
|:---------------------------------------|:------------------|----------------:|---------------:|---------:|-----------------:|
| ext_ram_top2_m10_v60_t100_f21_x2_5_GLD | external_ram      |          0.5097 |        -0.4098 |   1.2440 |           2.5000 |
| ext_ram_top1_m10_v10_t100_f21_x2_5_GLD | external_ram      |          0.4611 |        -0.4197 |   1.0987 |           2.5000 |
| ext_ram_top1_m10_v10_t100_f21_x2_5_GLD | external_ram      |          0.4430 |        -0.4197 |   1.0556 |           2.5000 |
| ext_ram_top1_m10_v10_t200_f21_x2_5_GLD | external_ram      |          0.4396 |        -0.4197 |   1.0475 |           2.5000 |
| ext_ram_top1_m10_v60_t200_f10_x2_0_GLD | external_ram      |          0.4333 |        -0.4094 |   1.0585 |           2.0000 |
| ext_ram_top1_m20_v60_t50_f10_x2_5_GLD  | external_ram      |          0.4294 |        -0.4252 |   1.0100 |           2.5000 |
| ext_ram_top2_m20_v10_t50_f10_x2_5_GLD  | external_ram      |          0.4236 |        -0.4079 |   1.0383 |           2.5000 |
| ext_ram_top1_m10_v60_t200_f10_x2_0_GLD | external_ram      |          0.4165 |        -0.5075 |   0.8207 |           2.0000 |
| ext_ram_top2_m20_v10_t50_f10_x2_5_GLD  | external_ram      |          0.4073 |        -0.4181 |   0.9741 |           2.5000 |
| ext_ram_top2_m10_v60_t100_f21_x2_5_GLD | external_ram      |          0.3979 |        -0.4252 |   0.9358 |           2.5000 |

Interpretation: this probe extends beyond the China ETF universe. It is still a research screen, not a tradable instruction.
