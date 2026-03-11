# DorkFi Liquidation Bot — Voi + Algorand

Automated liquidation bot for [DorkFi](https://dork.fi) on Voi Network and Algorand.

Monitors undercollateralized positions every 5 minutes across both chains and executes liquidations when profitable.

## How It Works

1. Polls `dorkfi.get_liquidation_candidates` via UluOS MCP gateway (both chains)
2. Filters out bad debt (collateral < $1) and healthy positions (HF >= 1.0)
3. Liquidates up to 50% of eligible borrow, capped at $50/trade
4. Signs and broadcasts transactions directly to Voi and Algorand mainnets

## Chains & Assets

| Chain | Repay Asset | Collateral Received | Bonus |
|-------|-------------|---------------------|-------|
| Voi | aUSDC (302190) | VOI | +10% |
| Algorand | USDC (31566704) | ALGO | +6% |

## Requirements

- Python 3.9+
- [UluOS](https://github.com/NautilusOSS/UluOS) MCP gateway running at `http://localhost:3000`
- Single wallet funded on both chains:
  - **Voi:** VOI for gas + aUSDC as liquidation capital
  - **Algorand:** ALGO for gas + USDC as liquidation capital
- Wallet opted into: aUSDC (Voi), USDC + UNIT + WAD (Algorand)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your wallet mnemonic
```

## Run

```bash
python3 liquidation_bot.py
```

Or via cron (every 5 min):
```bash
*/5 * * * * /path/to/liq_bot_run.sh
```

## Configuration

Edit the config block at the top of `liquidation_bot.py`:

| Variable | Default | Description |
|---|---|---|
| `WALLET` | — | Your liquidator wallet address (same on both chains) |
| `MAX_PER_TRADE` | $50 | Max USD per liquidation |

## Security

- Never commit your `.env` file
- Mnemonic loaded from `LIQUIDATION_BOT_MNEMONIC` environment variable only
- State tracked in `liq_bot_state.json` (gitignored)

## Disclaimer

Use at your own risk. Liquidation is competitive — positions may be taken by other bots. Always test with small amounts first.
