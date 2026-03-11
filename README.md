# DorkFi Liquidation Bot — Voi + Algorand

Automated liquidation bot for [DorkFi](https://dorkfi.com) on the Voi Network.

Monitors undercollateralized positions every 5 minutes and executes liquidations when profitable.

## How It Works

1. Polls `dorkfi.get_liquidation_candidates` via UluOS MCP gateway
2. Filters out bad debt (collateral < $1) and healthy positions (HF >= 1.0)
3. Liquidates up to 50% of eligible borrow, capped at $50/trade
4. Signs and broadcasts transactions directly to Voi mainnet

## Requirements

- Python 3.9+
- [UluOS](https://github.com/NautilusOSS/UluOS) MCP gateway running at `http://localhost:3000`
- Voi wallet funded with:
  - VOI for gas fees
  - aUSDC (asset ID `302190`) as liquidation capital

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
| `VOI_WALLET` | — | Your liquidator wallet address |
| `MAX_PER_TRADE` | $50 | Max aUSDC per liquidation |
| `VOI_SELL_PCT` | 15% | Max % of VOI holdings to sell per day |

## Security

- Never commit your `.env` file
- Mnemonic is loaded from `LIQUIDATION_BOT_MNEMONIC` environment variable only
- State tracked in `liq_bot_state.json` (gitignored)

## Disclaimer

Use at your own risk. Liquidation is competitive — positions may be taken by other bots. Always test with small amounts first.
