#!/usr/bin/env python3
"""
DorkFi Liquidation Bot — Voi + Algorand
Wallet: JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A
"""

import os, json, time, logging, subprocess, base64
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.openclaw/workspace/liq-bot.env"))

from algosdk import mnemonic, account, transaction, encoding
from algosdk.v2client import algod

# ── Config ─────────────────────────────────────────────────────────────────────
WALLET        = "JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A"
STATE_FILE    = os.path.expanduser("~/.openclaw/workspace/liq_bot_state.json")
LOG_FILE      = os.path.expanduser("~/.openclaw/workspace/liq_bot_output.log")
RUNNER        = os.path.expanduser("~/.openclaw/workspace/algo_liq_runner.mjs")
MAX_PER_TRADE = 200.0
MIN_PROFIT    = 0.50   # skip if estimated profit < $0.50

# Networks
VOI_NODE   = "https://mainnet-api.voi.nodely.dev"
ALGO_NODE  = "https://mainnet-api.4160.nodely.dev"
DORKFI_API = "https://dorkfi-api.nautilus.sh"

# Voi token IDs
AUSDC_VOI  = 302190

# Algo token IDs
USDC_ALGO  = 31566704

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Wallet ─────────────────────────────────────────────────────────────────────
BOT_MN      = os.environ.get("LIQUIDATION_BOT_MNEMONIC", "").strip()
private_key = mnemonic.to_private_key(BOT_MN)
assert account.address_from_private_key(private_key) == WALLET

voi_client  = algod.AlgodClient("", VOI_NODE,  headers={"X-Algo-API-Token": ""})
algo_client = algod.AlgodClient("", ALGO_NODE, headers={"X-Algo-API-Token": ""})

# ── State ──────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f: return json.load(f)
    return {"liquidations": []}

def save_state(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f, indent=2)

# ── API helpers ────────────────────────────────────────────────────────────────
import urllib.request

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "liq-bot/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def get_voi_candidates():
    data = fetch_json(f"{DORKFI_API}/user-health/liquidatable?network=voimain")
    return data.get("data", [])

def get_algo_candidates():
    result = subprocess.run(
        ["/usr/local/bin/node", RUNNER, "candidates", "algorand"],
        capture_output=True, text=True, timeout=30
    )
    d = json.loads(result.stdout)
    return d.get("candidates", [])

def get_voi_balances():
    d = fetch_json(f"{VOI_NODE}/v2/accounts/{WALLET}")
    voi   = d["amount"] / 1e6
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    ausdc = assets.get(AUSDC_VOI, 0) / 1e6
    return voi, ausdc

def get_algo_token_balance(asset_id):
    d = fetch_json(f"{ALGO_NODE}/v2/accounts/{WALLET}")
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    if asset_id == 0:
        return d["amount"] / 1e6
    return assets.get(asset_id, 0) / 1e6

# ── Contracts config (from DorkFiMCP) ─────────────────────────────────────────
with open(os.path.expanduser("~/DorkFiMCP/data/contracts.json")) as f:
    CONTRACTS = json.load(f)

def get_market_info(chain, symbol):
    markets = CONTRACTS.get(chain, {}).get("markets", [])
    for m in markets:
        if m.get("symbol") == symbol:
            return m
    return None

def get_liquidation_bonus(chain):
    return 0.10 if chain == "voi" else 0.06

# ── Voi liquidation ────────────────────────────────────────────────────────────
def liquidate_voi(borrower, collateral_symbol, debt_symbol, amount_usd, state):
    """Build and submit a Voi liquidation via DorkFiMCP runner."""
    log.info(f"  [VOI] Building liquidation txn: repay {amount_usd:.4f} {debt_symbol}, seize {collateral_symbol}")
    try:
        result = subprocess.run(
            ["/usr/local/bin/node", RUNNER, "build", borrower, collateral_symbol, debt_symbol,
             f"{amount_usd:.6f}", WALLET, "voi"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(result.stderr.strip() or result.stdout.strip())
        d = json.loads(result.stdout)
        if "error" in d: raise Exception(d["error"])
        txns = [base64.b64decode(t) for t in d["transactions"]]
        signed = [encoding.msgpack_decode(t).sign(private_key) for t in txns]
        txid = voi_client.send_transactions(signed)
        transaction.wait_for_confirmation(voi_client, txid, 8)
        log.info(f"  [VOI] Liquidation confirmed: {txid}")
        return txid
    except Exception as e:
        log.error(f"  [VOI] Liquidation failed: {e}")
        return None

def liquidate_algo(borrower, collateral_symbol, debt_symbol, amount_usd, state):
    """Build and submit an Algorand liquidation via DorkFiMCP runner."""
    log.info(f"  [ALGO] Building liquidation txn: repay {amount_usd:.4f} {debt_symbol}, seize {collateral_symbol}")
    try:
        result = subprocess.run(
            ["/usr/local/bin/node", RUNNER, "build", borrower, collateral_symbol, debt_symbol,
             f"{amount_usd:.6f}", WALLET, "algorand"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(result.stderr.strip() or result.stdout.strip())
        d = json.loads(result.stdout)
        if "error" in d: raise Exception(d["error"])
        txns = [base64.b64decode(t) for t in d["transactions"]]
        signed = [encoding.msgpack_decode(t).sign(private_key) for t in txns]
        txid = algo_client.send_transactions(signed)
        transaction.wait_for_confirmation(algo_client, txid, 8)
        log.info(f"  [ALGO] Liquidation confirmed: {txid}")
        return txid
    except Exception as e:
        log.error(f"  [ALGO] Liquidation failed: {e}")
        return None

# ── Main logic ─────────────────────────────────────────────────────────────────
def process_candidate(chain, c, state, liquidate_fn):
    borrower       = c.get("address", c.get("userAddress", ""))
    hf             = float(c.get("healthFactor", 1.0))
    borrow_usd     = float(c.get("totalBorrowUSD", c.get("totalBorrowValue", 0)))
    collateral_usd = float(c.get("totalCollateralUSD", c.get("totalCollateralValue", 0)))
    collateral_sym = c.get("collateralSymbol", "")
    debt_sym       = c.get("debtSymbol", "")

    log.info(f"  {borrower[:20]}... HF={hf:.4f} debt=${borrow_usd:.2f} coll=${collateral_usd:.2f} [{debt_sym}→{collateral_sym}]")

    if hf >= 1.0:
        log.info("  -> Skip: HF >= 1.0")
        return False

    if collateral_usd < 1.0:
        log.info(f"  -> Skip: bad debt (coll ${collateral_usd:.4f})")
        tg_send(f"⚠️ <b>DorkFi Bad Debt Detected</b>\n"
                f"Chain: {chain.upper()} | Pool: {c.get('poolId','?')}\n"
                f"Address: <code>{borrower}</code>\n"
                f"Debt: <b>${borrow_usd:.2f}</b> | Collateral: <b>${collateral_usd:.4f}</b>\n"
                f"HF: {hf:.4f} — not liquidatable (bad debt)")
        return False

    if collateral_usd < borrow_usd * 0.5:
        log.info(f"  -> Skip: collateral too low vs debt (likely bad debt)")
        return False

    bonus = get_liquidation_bonus(chain)
    repay = min(borrow_usd * 0.50, MAX_PER_TRADE)
    expected_gain = repay * bonus
    est_gas = 0.10
    est_profit = expected_gain - est_gas

    if est_profit < MIN_PROFIT:
        log.info(f"  -> Skip: estimated profit ${est_profit:.3f} < ${MIN_PROFIT}")
        return False

    # 🚨 Alert: profitable position found
    tg_send(f"🦈 <b>Liquidation Opportunity!</b>\n"
            f"Chain: {chain.upper()} | Pool: {c.get('poolId','?')}\n"
            f"Address: <code>{borrower}</code>\n"
            f"HF: <b>{hf:.4f}</b> | Debt: <b>${borrow_usd:.2f}</b> | Collateral: <b>${collateral_usd:.2f}</b>\n"
            f"Repay: <b>${repay:.2f} {debt_sym}</b> → seize <b>{collateral_sym}</b>\n"
            f"Est. profit: <b>${est_profit:.2f}</b>\n"
            f"Executing now...")

    # Check we have enough debt token to repay
    if chain == "voi":
        _, ausdc = get_voi_balances()
        if debt_sym in ("aUSDC", "WAD") and ausdc < repay:
            log.warning(f"  -> Skip: insufficient {debt_sym} (have ${ausdc:.2f}, need ${repay:.2f})")
            return False
    else:
        debt_market = get_market_info("algorand", debt_sym)
        if debt_market:
            asset_id = debt_market.get("assetId") or 0
            bal = get_algo_token_balance(asset_id)
            decimals = debt_market.get("decimals", 6)
            if bal < repay:
                log.warning(f"  -> Skip: insufficient {debt_sym} (have {bal:.4f}, need {repay:.4f})")
                return False

    txid = liquidate_fn(borrower, collateral_sym, debt_sym, repay, state)
    if txid:
        tg_send(f"✅ <b>Liquidation Successful!</b>\n"
                f"Chain: {chain.upper()}\n"
                f"Borrower: <code>{borrower}</code>\n"
                f"Repaid: <b>${repay:.2f} {debt_sym}</b> → seized <b>{collateral_sym}</b>\n"
                f"Est. profit: <b>${est_profit:.2f}</b>\n"
                f"TxID: <code>{txid}</code>")
        state["liquidations"].append({
            "ts": datetime.utcnow().isoformat(),
            "chain": chain,
            "account": borrower,
            "repaid_usd": repay,
            "collateral": collateral_sym,
            "debt": debt_sym,
            "txid": txid,
            "hf": hf
        })
        save_state(state)
        return True
    else:
        tg_send(f"❌ <b>Liquidation Failed</b>\n"
                f"Chain: {chain.upper()} | Borrower: <code>{borrower}</code>\n"
                f"Attempted: ${repay:.2f} {debt_sym} → {collateral_sym}\n"
                f"Check logs for details.")
    return False

def run():
    log.info("=== DorkFi Liquidation Bot ===")
    state = load_state()

    # ── Voi ──
    log.info("\n[VOI] Fetching candidates...")
    try:
        voi_candidates = get_voi_candidates()
        log.info(f"[VOI] {len(voi_candidates)} candidate(s)")
        for c in voi_candidates:
            process_candidate_v2("voi", c, state, liquidate_voi)
    except Exception as e:
        log.error(f"[VOI] Error: {e}")

    # ── Algorand ──
    log.info("\n[ALGO] Fetching candidates...")
    try:
        algo_candidates = get_algo_candidates()
        log.info(f"[ALGO] {len(algo_candidates)} candidate(s)")
        for c in algo_candidates:
            process_candidate_v2("algorand", c, state, liquidate_algo)
    except Exception as e:
        log.error(f"[ALGO] Error: {e}")

    log.info("\n=== Run complete ===")

if __name__ == "__main__":
    pass  # entry point at bottom

# ── Telegram Alerts ────────────────────────────────────────────────────────────
import urllib.parse

def _tg_token():
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            c = json.load(f)
        return c["channels"]["telegram"]["botToken"]
    except Exception:
        return None

TG_CHAT_ID = "6867273225"

def tg_send(msg):
    token = _tg_token()
    if not token:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=8)
    except Exception as e:
        log.warning(f"Telegram alert failed: {e}")

# ── Swap-and-Liquidate ─────────────────────────────────────────────────────────
SWAP_RUNNER = os.path.expanduser("~/.openclaw/workspace/swap_liq_runner.mjs")

# Tokens we can swap FROM (stable base assets)
STABLE_FROM = {
    "voi":      ["aUSDC", "WAD"],   # in order of preference
    "algorand": ["USDC"],
}

# Tokens we pre-fund and hold directly (no swap needed)
DIRECT_TOKENS = {
    "voi":      {"aUSDC", "WAD", "VOI"},
    "algorand": {"USDC", "ALGO"},
}

# Min liquidity USD required to trust a swap route
MIN_ROUTE_LIQUIDITY = 5000
MAX_PRICE_IMPACT = 0.03   # 3%
MIN_SWAP_PROFIT = 0.05    # min profit after slippage+gas before skipping


def get_quote(chain, from_sym, to_sym, amount_usd, sender):
    """Get swap quote via swap_liq_runner.mjs"""
    try:
        r = subprocess.run(
            ["/usr/local/bin/node", SWAP_RUNNER, "quote",
             chain, from_sym, to_sym, f"{amount_usd:.6f}", sender],
            capture_output=True, text=True, timeout=20
        )
        d = json.loads(r.stdout)
        if "error" in d:
            return None
        return d
    except Exception as e:
        log.warning(f"  Quote error: {e}")
        return None


def build_voi_swap(from_sym, to_sym, amount_usd, sender):
    """Build Voi swap txns via HumbleSwapMCP"""
    r = subprocess.run(
        ["/usr/local/bin/node", SWAP_RUNNER, "build_swap",
         "voi", from_sym, to_sym, f"{amount_usd:.6f}", sender],
        capture_output=True, text=True, timeout=30
    )
    d = json.loads(r.stdout)
    if "error" in d:
        raise Exception(d["error"])
    return d


def has_debt_token(chain, debt_sym, repay_usd):
    """Check if bot wallet has enough of the debt token to repay directly."""
    try:
        if chain == "voi":
            voi_bal, ausdc_bal = get_voi_balances()
            if debt_sym == "aUSDC":
                return ausdc_bal >= repay_usd
            if debt_sym == "VOI":
                return voi_bal * 0.004 >= repay_usd  # rough VOI price
            if debt_sym == "WAD":
                # WAD is ARC-200 — check algod for arc200 balance via MCP
                return ausdc_bal >= repay_usd  # proxy: we'd swap aUSDC→WAD
        if chain == "algorand":
            _, _, algo_bal, usdc_bal = get_algo_balances()
            if debt_sym == "USDC":
                return usdc_bal >= repay_usd
            if debt_sym == "ALGO":
                return algo_bal * 0.25 >= repay_usd  # rough ALGO price
    except Exception:
        pass
    return False


def get_algo_balances():
    d = fetch_json(f"https://mainnet-api.4160.nodely.dev/v2/accounts/{WALLET}")
    algo = d["amount"] / 1e6
    assets = {a["asset-id"]: a["amount"] for a in d.get("assets", [])}
    usdc = assets.get(31566704, 0) / 1e6
    return d["amount"] / 1e6, None, algo, usdc


def swap_and_liquidate_voi(borrower, collateral_sym, debt_sym, repay_usd, state):
    """Swap aUSDC/WAD → debt_sym, then liquidate."""
    from_options = STABLE_FROM["voi"]

    for from_sym in from_options:
        log.info(f"  [VOI] Checking swap route: {from_sym} → {debt_sym}")
        q = get_quote("voi", from_sym, debt_sym, repay_usd, WALLET)
        if not q or not q.get("routable"):
            log.info(f"  [VOI] No route for {from_sym}→{debt_sym}")
            continue

        # Check our balance of from_sym
        voi_bal, ausdc_bal = get_voi_balances()
        have = ausdc_bal if from_sym == "aUSDC" else 0
        if have < repay_usd * 1.02:  # 2% buffer for slippage
            log.warning(f"  [VOI] Insufficient {from_sym}: have ${have:.2f}, need ${repay_usd:.2f}")
            continue

        log.info(f"  [VOI] Route found via HumbleSwap, executing swap {from_sym}→{debt_sym} ${repay_usd:.2f}...")
        try:
            # Step 1: swap
            swap_data = build_voi_swap(from_sym, debt_sym, repay_usd, WALLET)
            import base64
            from algosdk import encoding, transaction
            txns = [base64.b64decode(t) for t in swap_data["transactions"]]
            signed = [encoding.msgpack_decode(t).sign(private_key) for t in txns]
            swap_txid = voi_client.send_transactions(signed)
            transaction.wait_for_confirmation(voi_client, swap_txid, 8)
            log.info(f"  [VOI] Swap confirmed: {swap_txid}")

            # Step 2: liquidate
            import time
            time.sleep(2)  # small buffer for indexer
            txid = liquidate_voi(borrower, collateral_sym, debt_sym, repay_usd, state)
            if txid:
                log.info(f"  [VOI] Swap-liquidation complete: {txid}")
                return txid
        except Exception as e:
            log.error(f"  [VOI] Swap-and-liquidate failed: {e}")
            return None

    return None


def process_candidate_v2(chain, c, state, liquidate_fn):
    """Extended process_candidate with swap fallback for non-held debt tokens."""
    borrower       = c.get("address", c.get("userAddress", ""))
    hf             = float(c.get("healthFactor", 1.0))
    borrow_usd     = float(c.get("totalBorrowUSD", c.get("totalBorrowValue", 0)))
    collateral_usd = float(c.get("totalCollateralUSD", c.get("totalCollateralValue", 0)))
    collateral_sym = c.get("collateralSymbol", "")
    debt_sym       = c.get("debtSymbol", "")

    # Auto-detect symbols if not provided by the candidate source
    all_syms = []
    if not debt_sym or not collateral_sym:
        pool_id = c.get("poolId", c.get("appId", 47139778))
        if chain == "voi":
            all_syms, debt_sym = detect_symbols_voi(borrower, pool_id)
            # collateral_sym will be tried from all_syms below
        if not debt_sym:
            log.info(f"  {borrower[:20]}... symbol detection failed — skip")
            return False
        if not collateral_sym and all_syms:
            # Will try each non-debt symbol as collateral
            collateral_sym = next((s for s in all_syms if s != debt_sym), None)

    # Build list of collateral candidates to try (detected or single)
    collateral_candidates = [s for s in all_syms if s != debt_sym] if all_syms else ([collateral_sym] if collateral_sym else [])

    log.info(f"  {borrower[:20]}... HF={hf:.4f} debt=${borrow_usd:.2f} coll=${collateral_usd:.2f} [{debt_sym}→{collateral_sym}]")

    if hf >= 1.0:
        return False
    if collateral_usd < 1.0 or collateral_usd < borrow_usd * 0.5:
        log.info(f"  -> Skip: bad debt (coll ${collateral_usd:.2f})")
        return False

    bonus = get_liquidation_bonus(chain)
    repay = min(borrow_usd * 0.50, MAX_PER_TRADE)
    max_seize = min(repay * (1 + bonus), collateral_usd)
    est_gas = 0.10

    # If no collateral candidates, nothing to do
    if not collateral_candidates:
        log.info(f"  -> Skip: no collateral candidates identified")
        return False

    # Check if we hold the debt token directly
    if has_debt_token(chain, debt_sym, repay):
        est_profit = max_seize - repay - est_gas
        if est_profit < MIN_PROFIT:
            log.info(f"  -> Skip: direct profit ${est_profit:.3f} < ${MIN_PROFIT}")
            return False
        tg_send(f"🦈 <b>Liquidation Opportunity (direct)</b>\n"
                f"Chain: {chain.upper()} | {borrower[:20]}...\n"
                f"HF: <b>{hf:.4f}</b> | Repay: <b>${repay:.2f} {debt_sym}</b>\n"
                f"Trying collateral: {collateral_candidates} | Est. profit: <b>${est_profit:.2f}</b>")
        txid = None
        for coll_try in collateral_candidates:
            txid = liquidate_fn(borrower, coll_try, debt_sym, repay, state)
            if txid:
                collateral_sym = coll_try
                break

    else:
        # Try swap route
        log.info(f"  -> No direct {debt_sym} balance, checking swap route...")
        stables = STABLE_FROM.get(chain, [])
        route_found = False
        for from_sym in stables:
            q = get_quote(chain, from_sym, debt_sym, repay, WALLET)
            if q and q.get("routable"):
                route_found = True
                swap_fee_pct = float(q.get("priceImpact") or 0.005)
                swap_cost = repay * swap_fee_pct + 0.05
                est_profit = max_seize - repay - swap_cost - est_gas
                if est_profit < MIN_SWAP_PROFIT:
                    log.info(f"  -> Skip: swap profit ${est_profit:.3f} < ${MIN_SWAP_PROFIT} (slippage+fees)")
                    break
                log.info(f"  -> Swap route: {from_sym}→{debt_sym} via {q.get('dex')} | est profit ${est_profit:.2f}")
                tg_send(f"🦈 <b>Liquidation Opportunity (swap)</b>\n"
                        f"Chain: {chain.upper()} | {borrower[:20]}...\n"
                        f"HF: <b>{hf:.4f}</b> | Swap: <b>{from_sym}→{debt_sym} ${repay:.2f}</b>\n"
                        f"Seize: <b>{collateral_sym}</b> | Est. profit: <b>${est_profit:.2f}</b>")
                if chain == "voi":
                    txid = None
                    for coll_try in collateral_candidates:
                        txid = swap_and_liquidate_voi(borrower, coll_try, debt_sym, repay, state)
                        if txid:
                            collateral_sym = coll_try
                            break
                else:
                    log.info(f"  -> Algorand swap-liquidate not yet automated — alert only")
                    return False
                break
        if not route_found:
            log.info(f"  -> Skip: no {debt_sym} balance and no swap route available")
            return False

    if txid:
        tg_send(f"✅ <b>Liquidation Successful!</b>\n"
                f"Chain: {chain.upper()} | <code>{borrower}</code>\n"
                f"Repaid: <b>{debt_sym}</b> → seized <b>{collateral_sym}</b>\n"
                f"TxID: <code>{txid}</code>")
        state["liquidations"].append({
            "ts": datetime.utcnow().isoformat(),
            "chain": chain, "account": borrower,
            "repaid_usd": repay, "collateral": collateral_sym,
            "debt": debt_sym, "txid": txid, "hf": hf
        })
        save_state(state)
        return True
    else:
        tg_send(f"❌ <b>Liquidation Failed</b>\n"
                f"Chain: {chain.upper()} | <code>{borrower}</code>\n"
                f"Attempted: ${repay:.2f} {debt_sym} → {collateral_sym}")
    return False

if __name__ == "__main__":
    run()


# ── Symbol auto-detection via box reads ────────────────────────────────────────

VOI_POOL_A_MARKETS = {
    41877720: ("VOI",   6),
    395614:   ("aUSDC", 6),
    420069:   ("UNIT",  8),
    40153155: ("POW",   6),
    413153:   ("aALGO", 6),
    40153308: ("aETH",  6),
    40153415: ("acbBTC",8),
    47138068: ("WAD",   6),
}
ALGO_POOL_B_MARKETS = {
    3346881192: ("xUSD",   6),
    3211805086: ("FINITE", 8),
}

def decode_user_box(value_hex):
    """Extract borrow/supply balance from a DorkFi user box.
    The box stores multiple uint256 fields; borrow balance is at a fixed offset."""
    data = bytes.fromhex(value_hex)
    # Scan 8-byte chunks for plausible non-zero balances (1e3 to 1e14 range)
    results = []
    for i in range(0, len(data) - 7, 4):
        val = int.from_bytes(data[i:i+8], 'big')
        if 1_000 < val < 10**14:
            results.append((i, val))
    return results

def detect_symbols_voi(borrower, pool_id):
    """Read on-chain box storage to determine all active market symbols for a borrower.
    Returns (list_of_all_symbols, debt_sym_guess) — caller should try all as collateral."""
    from algosdk import encoding as enc
    import base64, urllib.parse

    try:
        pk = enc.decode_address(borrower).hex()
        boxes_url = f"{VOI_NODE}/v2/applications/{pool_id}/boxes"
        req = urllib.request.Request(boxes_url, headers={"User-Agent": "liq-bot"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())

        user_boxes = []
        for box in d.get("boxes", []):
            name = base64.b64decode(box["name"]).hex()
            if pk[:16] in name and name.startswith("757365727334"):
                contract_id = int(name[-8:], 16)
                if contract_id in VOI_POOL_A_MARKETS:
                    user_boxes.append((contract_id, base64.b64decode(box["name"])))

        positions = {}
        for contract_id, box_bytes in user_boxes:
            sym, dec = VOI_POOL_A_MARKETS[contract_id]
            name_enc = urllib.parse.quote(base64.b64encode(box_bytes).decode(), safe='')
            url = f"{VOI_NODE}/v2/applications/{pool_id}/box?name=b64%3A{name_enc}"
            req = urllib.request.Request(url, headers={"User-Agent": "liq-bot"})
            val = json.loads(urllib.request.urlopen(req, timeout=10).read())
            value_bytes = base64.b64decode(val['value'])
            candidates = decode_user_box(value_bytes.hex())
            if candidates:
                small_vals = [v for _, v in candidates if v < 10**10]
                if small_vals:
                    balance = min(small_vals) / (10 ** dec)
                    positions[sym] = balance

        log.info(f"  Detected positions for {borrower[:16]}...: {positions}")
        if not positions:
            return [], None

        sorted_pos = sorted(positions.items(), key=lambda x: x[1])
        all_syms = [s for s, _ in sorted_pos]
        debt_guess = sorted_pos[0][0]  # smallest balance = likely debt
        return all_syms, debt_guess

    except Exception as e:
        log.warning(f"  Symbol detection failed: {e}")
    return [], None
