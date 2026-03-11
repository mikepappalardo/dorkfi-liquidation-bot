#!/usr/bin/env python3
"""
DorkFi Liquidation Bot — Voi + Algorand
Wallet: JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A

Strategy:
- Poll DorkFi for liquidation candidates on both chains every 5 min
- Liquidate up to 50% of eligible positions
- Max $50/trade to preserve capital
- Skip bad debt (collateral < $1)

Voi:   repay aUSDC, receive VOI (+10% bonus) — sell VOI slowly
Algo:  repay USDC, receive ALGO (+6% bonus)
"""

import os, json, time, logging, urllib.request, threading, queue, base64
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.openclaw/workspace/liq-bot.env"))

from algosdk import mnemonic, account, transaction, encoding
from algosdk.v2client import algod

# --- Config ---
WALLET        = "JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A"
MCP_URL       = "http://localhost:3000"
STATE_FILE    = os.path.expanduser("~/.openclaw/workspace/liq_bot_state.json")
LOG_FILE      = os.path.expanduser("~/.openclaw/workspace/liq_bot_output.log")
MAX_PER_TRADE = 50.0   # max USD per liquidation

# Voi
VOI_NODELY    = "https://mainnet-api.voi.nodely.dev"
AUSDC_VOI_ID  = 302190

# Algorand
ALGO_NODE     = "https://mainnet-api.algonode.cloud"
USDC_ALGO_ID  = 31566704

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# --- Wallet ---
BOT_MNEMONIC = os.environ.get("LIQUIDATION_BOT_MNEMONIC", "")
if not BOT_MNEMONIC:
    raise EnvironmentError("LIQUIDATION_BOT_MNEMONIC not set in environment")
private_key  = mnemonic.to_private_key(BOT_MNEMONIC)
assert account.address_from_private_key(private_key) == WALLET, "Key mismatch"

voi_client  = algod.AlgodClient("", VOI_NODELY, headers={"X-Algo-API-Token": ""})
algo_client = algod.AlgodClient("", ALGO_NODE)

# --- State ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"liquidations": []}

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

# --- Balances ---
def get_voi_balances():
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"{VOI_NODELY}/v2/accounts/{WALLET}"
    r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8)
    d = json.loads(r.read())
    voi   = d["amount"] / 1e6
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    ausdc = assets.get(AUSDC_VOI_ID, 0) / 1e6
    return voi, ausdc

def get_algo_balances():
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"{ALGO_NODE}/v2/accounts/{WALLET}"
    r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8)
    d = json.loads(r.read())
    algo  = d["amount"] / 1e6
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    usdc  = assets.get(USDC_ALGO_ID, 0) / 1e6
    return algo, usdc

# --- Liquidation runner ---
def run_chain(chain, client, capital_bal, state):
    chain_label = chain.upper()
    debt_symbol = "aUSDC" if chain == "voi" else "USDC"

    log.info(f"[{chain_label}] Fetching liquidation candidates...")
    try:
        result = mcp_call("dorkfi.get_liquidation_candidates", {"chain": chain})
    except Exception as e:
        log.error(f"[{chain_label}] Failed to get candidates: {e}")
        return capital_bal

    candidates = result.get("candidates", []) if isinstance(result, dict) else result
    log.info(f"[{chain_label}] {len(candidates)} candidate(s) | capital: ${capital_bal:.2f} {debt_symbol}")

    for c in candidates:
        acct           = c.get("address", c.get("account", ""))
        hf             = float(c.get("healthFactor", 1.0))
        borrow_usd     = float(c.get("totalBorrowUSD", c.get("totalBorrowUsd", 0)))
        collateral_usd = float(c.get("totalCollateralUSD", c.get("totalCollateralUsd", 0)))

        log.info(f"  {acct[:12]}... HF={hf:.4f} borrow=${borrow_usd:.2f} collateral=${collateral_usd:.2f}")

        if hf >= 1.0:
            log.info("  -> Skipping, HF >= 1.0")
            continue
        if collateral_usd < 1.0:
            log.info(f"  -> Skipping, bad debt (collateral ${collateral_usd:.4f})")
            continue

        repay = min(borrow_usd * 0.50, MAX_PER_TRADE, capital_bal * 0.95)
        if repay < 1.0:
            log.info(f"  -> Skipping, repay too small (${repay:.2f})")
            continue

        collateral_symbol = c.get("collateralSymbol", "ALGO" if chain == "algorand" else "VOI")
        log.info(f"  -> Liquidating: repay ${repay:.2f} {debt_symbol} | seize {collateral_symbol}")

        try:
            txn_data = mcp_call("dorkfi.liquidate_txn", {
                "chain": chain,
                "borrower": acct,
                "collateral_symbol": collateral_symbol,
                "debt_symbol": debt_symbol,
                "amount": f"{repay:.6f}",
                "sender": WALLET
            })

            txns = txn_data.get("transactions", [])
            if not txns:
                log.error("  -> No transactions returned")
                continue

            signed_group = [encoding.msgpack_decode(t).sign(private_key) for t in txns]
            txid = client.send_transactions(signed_group)
            log.info(f"  -> Success! TxID: {txid}")

            state["liquidations"].append({
                "ts": datetime.utcnow().isoformat(),
                "chain": chain,
                "account": acct,
                "repaid_usd": repay,
                "collateral": collateral_symbol,
                "txid": txid,
                "hf": hf
            })
            capital_bal -= repay
            save_state(state)

        except Exception as e:
            log.error(f"  -> Failed: {e}")
            continue

    return capital_bal

# --- Main ---
def run():
    log.info("=== DorkFi Liquidation Bot Starting (Voi + Algorand) ===")
    state = load_state()

    # Voi
    voi_bal, ausdc_bal = get_voi_balances()
    log.info(f"[VOI]  {voi_bal:,.2f} VOI | ${ausdc_bal:.3f} aUSDC")
    if ausdc_bal >= 1.0:
        ausdc_bal = run_chain("voi", voi_client, ausdc_bal, state)
    else:
        log.warning("[VOI] Insufficient aUSDC — skipping")

    # Algorand
    algo_bal, usdc_bal = get_algo_balances()
    log.info(f"[ALGO] {algo_bal:.4f} ALGO | ${usdc_bal:.3f} USDC")
    if usdc_bal >= 1.0:
        usdc_bal = run_chain("algorand", algo_client, usdc_bal, state)
    else:
        log.warning("[ALGO] Insufficient USDC — skipping")

    log.info("=== Run complete ===")

if __name__ == "__main__":
    run()
