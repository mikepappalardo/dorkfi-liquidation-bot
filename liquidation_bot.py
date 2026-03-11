#!/usr/bin/env python3
"""
DorkFi Liquidation Bot — Voi Network
Wallet: JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A
Capital: ~$110 aUSDC + 100K VOI

Strategy:
- Poll DorkFi for liquidation candidates every 5 min
- Liquidate up to 60% of eligible positions
- Sell received VOI slowly (10-20%/day) to avoid cascade
- Max $50/trade to preserve capital
"""

import os, json, time, logging, urllib.request, threading, queue, base64
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.openclaw/workspace/wad-bot.env"))

from algosdk import mnemonic, account, transaction, encoding
from algosdk.v2client import algod

# --- Config ---
VOI_WALLET    = "JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A"
AUSDC_ID      = 302190
VOI_NODELY    = "https://mainnet-api.voi.nodely.dev"
MCP_URL       = "http://localhost:3000"
STATE_FILE    = os.path.expanduser("~/.openclaw/workspace/liq_bot_state.json")
LOG_FILE      = os.path.expanduser("~/.openclaw/workspace/liq_bot_output.log")
MAX_PER_TRADE = 50.0   # max aUSDC per liquidation
MIN_DEVIATION = 0.05   # only liquidate if health factor < 0.95 (5% buffer)
VOI_SELL_PCT  = 0.15   # sell 15% of VOI holdings per day max

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# --- Wallet ---
BOT_MNEMONIC = os.environ.get("LIQUIDATION_BOT_MNEMONIC", "")
if not BOT_MNEMONIC:
    raise EnvironmentError("LIQUIDATION_BOT_MNEMONIC not set in environment")
private_key  = mnemonic.to_private_key(BOT_MNEMONIC)
assert account.address_from_private_key(private_key) == VOI_WALLET, "Key mismatch — check LIQUIDATION_BOT_MNEMONIC"
voi_client   = algod.AlgodClient("", VOI_NODELY, headers={"X-Algo-API-Token": ""})

# --- State ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"liquidations": [], "voi_received": 0, "voi_sold": 0, "last_sell_ts": 0}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

# --- MCP SSE helper ---
def mcp_call(tool_name, args, timeout=30):
    results = queue.Queue()
    session_q = queue.Queue()

    def sse_listen():
        req = urllib.request.Request(f"{MCP_URL}/mcp/sse", headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=timeout + 5) as r:
            event_type = None
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("event:"): event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if event_type == "endpoint": session_q.put(data)
                    else: results.put(data)
                elif line == "": event_type = None

    t = threading.Thread(target=sse_listen, daemon=True)
    t.start()
    endpoint = session_q.get(timeout=10)
    msg_url = f"{MCP_URL}{endpoint}"

    def post(payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(msg_url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)

    post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "liq-bot", "version": "1"}}})
    time.sleep(0.3)
    post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": tool_name, "arguments": args}})

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = results.get(timeout=1)
            m = json.loads(msg)
            if m.get("id") == 2:
                content = m.get("result", {}).get("content", [{}])
                text = content[0].get("text", "")
                if m.get("result", {}).get("isError"):
                    raise Exception(f"MCP error: {text}")
                return json.loads(text)
        except queue.Empty:
            pass
    raise TimeoutError(f"MCP call {tool_name} timed out")

# --- Get balances ---
def get_balances():
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"{VOI_NODELY}/v2/accounts/{VOI_WALLET}"
    r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8)
    d = json.loads(r.read())
    voi  = d["amount"] / 1e6
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    ausdc = assets.get(AUSDC_ID, 0) / 1e6
    return voi, ausdc

# --- Main loop ---
def run():
    log.info("=== DorkFi Liquidation Bot Starting ===")
    log.info(f"Wallet: {VOI_WALLET}")

    state = load_state()
    voi_bal, ausdc_bal = get_balances()
    log.info(f"Balances: {voi_bal:,.2f} VOI | ${ausdc_bal:.3f} aUSDC")

    if ausdc_bal < 1.0:
        log.warning("Insufficient aUSDC — need at least $1 to liquidate")
        return

    # Get liquidation candidates
    log.info("Fetching liquidation candidates...")
    try:
        candidates = mcp_call("dorkfi.get_liquidation_candidates", {"chain": "voi"})
    except Exception as e:
        log.error(f"Failed to get candidates: {e}")
        return

    # Response is {"candidates": [...], ...}
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", [])

    if not candidates:
        log.info("No liquidation candidates found")
        return

    log.info(f"Found {len(candidates)} candidate(s)")

    for c in candidates:
        acct       = c.get("address", c.get("account", ""))
        hf         = float(c.get("healthFactor", c.get("health_factor", 1.0)))
        borrow_usd = float(c.get("totalBorrowUSD", c.get("totalBorrowUsd", c.get("borrow_usd", 0))))

        collateral_usd = float(c.get("totalCollateralUSD", c.get("totalCollateralUsd", 0)))
        log.info(f"  {acct[:12]}... HF={hf:.4f} borrow=${borrow_usd:.2f} collateral=${collateral_usd:.2f}")

        if hf >= 1.0:
            log.info(f"  -> Skipping, HF >= 1.0 (not yet liquidatable)")
            continue

        if collateral_usd < 1.0:
            log.info(f"  -> Skipping, bad debt (collateral ${collateral_usd:.4f} < $1) — nothing to seize")
            continue

        # Amount to repay: 50% of borrow, capped at MAX_PER_TRADE and available aUSDC
        repay = min(borrow_usd * 0.50, MAX_PER_TRADE, ausdc_bal * 0.95)
        if repay < 1.0:
            log.info(f"  -> Skipping, repay amount too small (${repay:.2f})")
            continue

        log.info(f"  -> Liquidating: repay ${repay:.2f} aUSDC")

        try:
            # Default: VOI collateral, aUSDC debt (most common on Voi DorkFi)
            collateral_symbol = c.get("collateralSymbol", c.get("collateral_symbol", "VOI"))
            debt_symbol       = c.get("debtSymbol", c.get("debt_symbol", "aUSDC"))

            txn_data = mcp_call("dorkfi.liquidate_txn", {
                "chain": "voi",
                "borrower": acct,
                "collateral_symbol": collateral_symbol,
                "debt_symbol": debt_symbol,
                "amount": f"{repay:.6f}",
                "sender": VOI_WALLET
            })

            txns = txn_data.get("transactions", [])
            if not txns:
                log.error("  -> No transactions returned")
                continue

            # Sign and submit — decode via algosdk to preserve group ID
            signed_group = []
            for txn_b64 in txns:
                txn_obj = encoding.msgpack_decode(txn_b64)
                signed_group.append(txn_obj.sign(private_key))

            txid = voi_client.send_transactions(signed_group)
            log.info(f"  -> Liquidation submitted! TxID: {txid}")

            state["liquidations"].append({
                "ts": datetime.utcnow().isoformat(),
                "account": acct,
                "repaid_usd": repay,
                "txid": txid,
                "hf_at_liq": hf
            })
            ausdc_bal -= repay
            save_state(state)

        except Exception as e:
            log.error(f"  -> Liquidation failed: {e}")
            continue

    log.info("=== Run complete ===")

if __name__ == "__main__":
    run()
