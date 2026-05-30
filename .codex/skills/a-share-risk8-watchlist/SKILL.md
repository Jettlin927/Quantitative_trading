---
name: a-share-risk8-watchlist
description: Generate A-share watchlists from this repository's current best Risk8 cross-section strength strategy. Use when the user asks to screen stocks, find candidate/observation tickets, get entry/exit reference levels, exclude ChiNext/STAR Market due to permissions, or validate the latest local market against the current optimal strategy in the Quantitative_trading workspace.
---

# A Share Risk8 Watchlist

Use this skill to answer practical screening requests in the local A-share quantitative research workspace. The strategy source of truth is the repository's current fixed specification, not memory or chat history:

- `docs/research/executable-strategy-cross-section-risk8.json`
- Its `evidenceRun` folder under `docs/research/runs/`
- The matching `context.json` and `strategies.json`

At creation time, the current candidate is `105-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-limitdelay-slip10bp-gap`: three-year hard gates pass, rolling-window robustness does not. Always state this boundary when presenting candidate stocks.

## Workflow

1. Confirm the request is research/screening only. Do not present results as investment advice, trading instruction, or guaranteed returns.
2. Run the bundled script from the repository root, preferably inside the API container:

   ```powershell
   docker compose exec -T api python .codex/skills/a-share-risk8-watchlist/scripts/generate_watchlist.py --date latest --top 30
   ```

3. Keep the default board exclusion when the user lacks ChiNext/STAR permissions. The script excludes `300/301/688/689` by default.
4. Interpret `mode` carefully:
   - `ACTIONABLE_BY_STRATEGY`: market breadth gate passed for the entry date; candidates can be discussed as strategy candidates.
   - `OBSERVE_ONLY_MARKET_RISK_OFF`: stock-level signals exist, but the market breadth gate failed; present them as watchlist only.
5. When the user asks for entry/exit points, use the script's reference fields:
   - `buy_ref`: close adjusted by configured buy slippage.
   - `stop`: hard stop reference.
   - `tp1`: first take-profit reference, sell half.
   - `tp2`: second take-profit reference, exit all.
   - `qty_for_capital`: reference sizing for the supplied capital, not a recommendation.

## Defaults

- Strategy preset: read from the fixed spec, normally `trend-follow-maximum-profit-no-macd`.
- Entry gate: previous-trading-day market breadth with MA20 >= 45%, MA60 >= 35%, up ratio >= 45%, samples >= 1000.
- Stock entry: MA trend-follow, volume confirmation, high-liquidity universe, cross-section strength ranking.
- Risk filters: entry-day range <= 8%, intraday return <= 10%, entry gap <= 6%.
- Execution stress: 0.1% buy/sell slippage, gap-stop at open, limit-down stop delay.
- Exits: 5% stop, 10% sell half and move stop to breakeven, 20% exit all.
- Universe: exclude ST, Beijing exchange, listing age under 365 days, low liquidity, low market cap, low turnover.

## Script Options

Useful examples:

```powershell
docker compose exec -T api python .codex/skills/a-share-risk8-watchlist/scripts/generate_watchlist.py --date 2026-05-29 --top 20
docker compose exec -T api python .codex/skills/a-share-risk8-watchlist/scripts/generate_watchlist.py --date latest --capital 200000 --top 50 --format json
docker compose exec -T api python .codex/skills/a-share-risk8-watchlist/scripts/generate_watchlist.py --include-no-permission-boards
```

If Docker is unavailable, ask the user to start the local system. Do not rewrite a parallel SQL or indicator implementation unless the project helpers are broken.
