# DorkFi Liquidation Bot

Automated liquidation bot for DorkFi lending markets on Voi Network and Algorand.

## Features

- **Multi-chain**: Monitors Voi Pool A/B and Algorand Pool A/B simultaneously
- **Direct liquidation**: Uses held aUSDC/USDC/VOI/ALGO to repay debt and seize collateral
- **Swap-and-liquidate**: When debt token isn't held, routes through HumbleSwap (Voi) or Pact (Algorand) to acquire it on-the-fly
- **Telegram alerts**: Notifies on opportunities found, successful liquidations, failures, and bad debt
- **Bad debt detection**: Skips positions where collateral < 50% of debt (guaranteed loss)
- **Profitability checks**: Accounts for swap slippage, gas, and liquidation bonus before executing
- **Monitoring dashboard**: Local web UI at `localhost:8768` showing live positions, bad debt, and risk radar

## Architecture

```
liquidation_bot.py      — Main bot (Python), runs every 5 min via cron
algo_liq_runner.mjs     — Fetches candidates + builds liquidation txns (DorkFiMCP)
swap_liq_runner.mjs     — Swap quotes + builds swap txns (HumbleSwapMCP / Pact)
dashboard/
  server.mjs            — Local API server (Node.js)
  index.html            — Dashboard UI
```

## Swap-and-Liquidate Flow

```
Opportunity detected (HF < 1.0, collateral > 50% of debt)
       │
       ▼
Do we hold the debt token?
  YES → liquidate directly
  NO  → get swap quote (HumbleSwap on Voi, Pact on Algorand)
           ├─ No route → skip
           ├─ Profit after slippage < $0.05 → skip
           └─ Route + profitable → swap → liquidate → Telegram alert
```

## Token Coverage

| Chain | Debt tokens (direct) | Debt tokens (via swap) |
|---|---|---|
| Voi | aUSDC, VOI, WAD | UNIT, POW, aALGO, aETH, acbBTC |
| Algorand | USDC, ALGO | USDC→debt via Pact (where pools exist) |

## Setup

```bash
pip install -r requirements.txt
cp liq-bot.env.example liq-bot.env  # add LIQUIDATION_BOT_MNEMONIC
crontab -e  # add: */5 * * * * /path/to/liq_bot_run.sh

# Dashboard
cd dashboard && node server.mjs
```

## Dependencies

- [DorkFiMCP](https://github.com/nautilus-oss/DorkFiMCP) — liquidation tx builder
- [HumbleSwapMCP](https://github.com/nautilus-oss/HumbleSwapMCP) — Voi DEX swap builder
- algosdk, python-dotenv

## Wallet

Bot wallet: `JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A` (enVoi: gandolfthegrey.voi)

Capital held: aUSDC (Voi), USDC (Algorand), VOI for gas
