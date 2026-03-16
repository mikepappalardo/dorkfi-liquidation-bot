/**
 * Swap-and-Liquidate Runner
 * Builds: [swap stablecoin → debt token] then [liquidate] as separate tx groups
 * Usage:
 *   node swap_liq_runner.mjs quote <chain> <fromSymbol> <toSymbol> <amountUSD> <sender>
 *   node swap_liq_runner.mjs build_swap <chain> <fromSymbol> <toSymbol> <amountUSD> <sender>
 *   node swap_liq_runner.mjs build_liq <chain> <borrower> <collateral> <debt> <amountUSD> <sender>
 */

import { prepareSwap } from '/Users/michaelpappalardo/HumbleSwapMCP/lib/builders.js';
import { prepareLiquidation } from '/Users/michaelpappalardo/DorkFiMCP/lib/builders.js';
import { resolveToken } from '/Users/michaelpappalardo/HumbleSwapMCP/lib/tokens.js';
import { findBestPool } from '/Users/michaelpappalardo/HumbleSwapMCP/lib/pools.js';
import { getTokenContractId } from '/Users/michaelpappalardo/HumbleSwapMCP/lib/tokens.js';

// ── Algorand DEX routing (Tinyman + Pact via REST) ─────────────────────────────

const TINYMAN_API = 'https://mainnet.analytics.tinyman.org/api/v1';
const PACT_API = 'https://api.pact.fi/api';

async function fetchJSON(url) {
  const r = await fetch(url, { headers: { 'User-Agent': 'dorkfi-liq-bot/1.0' } });
  if (!r.ok) throw new Error(`HTTP ${r.status} from ${url}`);
  return r.json();
}

// Find best Algorand swap route: direct pool or via ALGO/USDC hop
async function findAlgoRoute(fromAssetId, toAssetId) {
  // Try direct pool first
  try {
    const pools = await fetchJSON(`${PACT_API}/pools?primaryAssetId=${fromAssetId}&secondaryAssetId=${toAssetId}&limit=5`);
    const direct = pools.results?.filter(p => p.version >= 200 && p.liquidity_in_usd > 1000);
    if (direct?.length > 0) {
      direct.sort((a, b) => b.liquidity_in_usd - a.liquidity_in_usd);
      return { type: 'direct', pool: direct[0], hops: 1 };
    }
  } catch {}

  // Try via ALGO (0) as intermediate
  const ALGO = 0;
  try {
    const [leg1, leg2] = await Promise.all([
      fetchJSON(`${PACT_API}/pools?primaryAssetId=${fromAssetId}&secondaryAssetId=${ALGO}&limit=3`),
      fetchJSON(`${PACT_API}/pools?primaryAssetId=${ALGO}&secondaryAssetId=${toAssetId}&limit=3`),
    ]);
    const l1 = leg1.results?.filter(p => p.liquidity_in_usd > 500)?.[0];
    const l2 = leg2.results?.filter(p => p.liquidity_in_usd > 500)?.[0];
    if (l1 && l2) return { type: 'via_algo', pool1: l1, pool2: l2, hops: 2 };
  } catch {}

  return null;
}

// Get Pact quote
async function getPactQuote(poolOnChainId, assetIn, amountIn) {
  try {
    const r = await fetchJSON(`${PACT_API}/pools/${poolOnChainId}/quote?assetInId=${assetIn}&amount=${amountIn}`);
    return { amountOut: r.amount_out, priceImpact: r.price_impact };
  } catch {
    return null;
  }
}

// ── Token registry for Algorand ─────────────────────────────────────────────────

const ALGO_TOKENS = {
  USDC:   { assetId: 31566704, decimals: 6 },
  ALGO:   { assetId: 0,        decimals: 6 },
  ALGO_NATIVE: { assetId: 0,   decimals: 6 },
  WAD:    { assetId: 3334160924, decimals: 6 },
  UNIT:   { assetId: 3121954282, decimals: 0 },
  FINITE: { assetId: 400593267,  decimals: 8 },
  FOLKS:  { assetId: 664900561,  decimals: 6 },
  COOP:   { assetId: 796425061,  decimals: 6 },
  xUSD:   { assetId: 760037151,  decimals: 6 },
  COMPX:  { assetId: 796425061,  decimals: 6 },
  goBTC:  { assetId: 386192725,  decimals: 8 },
  wETH:   { assetId: 386195940,  decimals: 8 },
};

function getAlgoTokenInfo(symbol) {
  return ALGO_TOKENS[symbol] || null;
}

// ── Quote endpoint ──────────────────────────────────────────────────────────────

async function quote(chain, fromSymbol, toSymbol, amountUSD, sender) {
  if (chain === 'voi') {
    const from = await resolveToken('voi', fromSymbol);
    const to = await resolveToken('voi', toSymbol);
    if (!from || !to) throw new Error(`Token not found on Voi: ${fromSymbol} or ${toSymbol}`);

    const fromId = getTokenContractId(from);
    const toId = getTokenContractId(to);
    const pool = await findBestPool('voi', fromId, toId);
    if (!pool) throw new Error(`No Humble pool found for ${fromSymbol}/${toSymbol}`);

    return {
      chain, fromSymbol, toSymbol, amountUSD,
      dex: 'HumbleSwap',
      poolId: pool.poolId,
      liquidityUSD: pool.liquidityUSD || null,
      routable: true,
    };
  }

  if (chain === 'algorand') {
    const fromToken = getAlgoTokenInfo(fromSymbol);
    const toToken = getAlgoTokenInfo(toSymbol);
    if (!fromToken || !toToken) throw new Error(`Token not found: ${fromSymbol} or ${toSymbol}`);

    const route = await findAlgoRoute(fromToken.assetId, toToken.assetId);
    if (!route) return { chain, fromSymbol, toSymbol, amountUSD, routable: false, reason: 'No DEX route found' };

    // Get rough quote
    const amountIn = Math.round(amountUSD * Math.pow(10, fromToken.decimals));
    let amountOut = null, priceImpact = null;
    if (route.type === 'direct') {
      const q = await getPactQuote(route.pool.on_chain_id, fromToken.assetId, amountIn);
      if (q) { amountOut = q.amountOut; priceImpact = q.priceImpact; }
    }

    const amountOutHuman = amountOut ? amountOut / Math.pow(10, toToken.decimals) : null;

    return {
      chain, fromSymbol, toSymbol, amountUSD,
      dex: 'Pact',
      route: route.type,
      hops: route.hops,
      routable: true,
      amountOut: amountOutHuman,
      priceImpact,
      poolId: route.pool?.on_chain_id || null,
    };
  }

  throw new Error(`Unknown chain: ${chain}`);
}

// ── Build swap txns (Voi via HumbleSwapMCP) ─────────────────────────────────────

async function buildVoiSwap(fromSymbol, toSymbol, amountUSD, sender) {
  const result = await prepareSwap('voi', fromSymbol, toSymbol, amountUSD, sender, 2);
  const txns = result.transactions.map(t => Buffer.from(t).toString('base64'));
  return {
    transactions: txns,
    details: result.details,
  };
}

// ── Build liquidation txns ──────────────────────────────────────────────────────

async function buildLiq(chain, borrower, collateralSymbol, debtSymbol, amountUSD, sender) {
  const result = await prepareLiquidation(chain, borrower, collateralSymbol, debtSymbol, amountUSD, sender);
  const txns = result.transactions.map(t => Buffer.from(t).toString('base64'));
  return { transactions: txns, details: result.details };
}

// ── CLI ─────────────────────────────────────────────────────────────────────────

const [, , cmd, ...args] = process.argv;

try {
  if (cmd === 'quote') {
    const [chain, fromSymbol, toSymbol, amountUSD, sender] = args;
    const result = await quote(chain, fromSymbol, toSymbol, parseFloat(amountUSD), sender);
    console.log(JSON.stringify(result));

  } else if (cmd === 'build_swap') {
    const [chain, fromSymbol, toSymbol, amountUSD, sender] = args;
    if (chain !== 'voi') throw new Error('build_swap only supports voi (use Pact UI for Algorand swaps)');
    const result = await buildVoiSwap(fromSymbol, toSymbol, parseFloat(amountUSD), sender);
    console.log(JSON.stringify(result));

  } else if (cmd === 'build_liq') {
    const [chain, borrower, collateralSymbol, debtSymbol, amountUSD, sender] = args;
    const result = await buildLiq(chain, borrower, collateralSymbol, debtSymbol, parseFloat(amountUSD), sender);
    console.log(JSON.stringify(result));

  } else {
    throw new Error(`Unknown command: ${cmd}. Use: quote | build_swap | build_liq`);
  }
} catch (e) {
  console.error(JSON.stringify({ error: e.message }));
  process.exit(1);
}
