import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { getLiquidationCandidates } from '/Users/michaelpappalardo/DorkFiMCP/lib/liquidation.js';
import { fetchUserHealthAll } from '/Users/michaelpappalardo/DorkFiMCP/lib/api.js';

// ── Market configs ──────────────────────────────────────────────────────────
const VOI_POOL_A_MARKETS = {
  41877720:  { sym: 'VOI',    dec: 6, std: 'network' },
  395614:    { sym: 'aUSDC',  dec: 6, std: 'asa'     },
  420069:    { sym: 'UNIT',   dec: 8, std: 'arc200'  },
  40153155:  { sym: 'POW',    dec: 6, std: 'asa'     },
  413153:    { sym: 'aALGO',  dec: 6, std: 'asa'     },
  40153308:  { sym: 'aETH',   dec: 6, std: 'asa'     },
  40153415:  { sym: 'acbBTC', dec: 8, std: 'asa'     },
  47138068:  { sym: 'WAD',    dec: 6, std: 'arc200'  },
};
const ALGO_POOL_A_MARKETS = {
  0:          { sym: 'ALGO',   dec: 6 },
  31566704:   { sym: 'USDC',   dec: 6 },
  3121954282: { sym: 'UNIT',   dec: 8 },
  2994233666: { sym: 'POW',    dec: 6 },
  386192725:  { sym: 'goBTC',  dec: 8 },
  2320775407: { sym: 'aVOI',   dec: 6 },
  1058926737: { sym: 'wBTC',   dec: 8 },
  386195940:  { sym: 'goETH',  dec: 8 },
  887406851:  { sym: 'wETH',   dec: 8 },
  1200094857: { sym: 'LINK',   dec: 8 },
  887648583:  { sym: 'SOL',    dec: 8 },
  893309613:  { sym: 'AVAX',   dec: 8 },
};
const ALGO_POOL_B_MARKETS = {
  400593267:  { sym: 'FINITE', dec: 8 },
  3203964481: { sym: 'FOLKS',  dec: 6 },
  796425061:  { sym: 'COOP',   dec: 6 },
  3178895177: { sym: 'HOG',    dec: 6 },
  312769:     { sym: 'USDt',   dec: 6 },
  760037151:  { sym: 'xUSD',   dec: 6 },
  2494786278: { sym: 'MONKO',  dec: 6 },
  3160000000: { sym: 'HAY',    dec: 6 },
  2637100337: { sym: 'BRO',    dec: 6 },
  2726252423: { sym: 'ALPHA',  dec: 6 },
  1732165149: { sym: 'COMPX',  dec: 6 },
  523683256:  { sym: 'AKTA',   dec: 6 },
  1096015467: { sym: 'PEPE',   dec: 4 },
  246516580:  { sym: 'GOLD$',  dec: 6 },
  2200000000: { sym: 'TINY',   dec: 6 },
};
const VOI_SCALE = 8000;

// ── Liquidation thresholds (approx LT from DorkFi market params) ────────────
const LT_MAP = {
  VOI: 0.75, aUSDC: 0.85, UNIT: 0.80, WAD: 0.82, POW: 0.70,
  aALGO: 0.78, aETH: 0.78, acbBTC: 0.78,
  ALGO: 0.75, USDC: 0.85, goBTC: 0.78, wBTC: 0.78, goETH: 0.78,
  wETH: 0.78, LINK: 0.70, SOL: 0.70, AVAX: 0.70, aVOI: 0.75,
  FINITE: 0.65, xUSD: 0.85, FOLKS: 0.65, COOP: 0.60, HOG: 0.60,
  USDt: 0.85, MONKO: 0.60, HAY: 0.65, BRO: 0.60, ALPHA: 0.60,
  COMPX: 0.65, AKTA: 0.60, PEPE: 0.55, 'GOLD$': 0.65, TINY: 0.60,
};

async function decodeVoiBoxes(address, poolId) {
  const pkHex = Buffer.from(
    (await import('/Users/michaelpappalardo/DorkFiMCP/node_modules/algosdk/dist/cjs/index.js'))
      .default.decodeAddress(address).publicKey
  ).toString('hex');

  const boxListUrl = `${VOI_NODE}/v2/applications/${poolId}/boxes`;
  const boxList = await fetchExt(boxListUrl);

  const positions = [];
  for (const box of (boxList.boxes || [])) {
    const nameHex = Buffer.from(box.name, 'base64').toString('hex');
    if (!nameHex.startsWith('757365727334') || !nameHex.includes(pkHex.slice(0, 16))) continue;
    const contractId = parseInt(nameHex.slice(-8), 16);
    const market = VOI_POOL_A_MARKETS[contractId];
    if (!market) continue;

    const nameB64 = encodeURIComponent(box.name);
    const valRes = await fetchExt(`${VOI_NODE}/v2/applications/${poolId}/box?name=b64%3A${nameB64}`);
    const data = Buffer.from(valRes.value, 'base64');

    // Scan 8-byte chunks for plausible balance values (1e3 → 1e13)
    const found = [];
    for (let i = 0; i < data.length - 7; i += 4) {
      const v = Number(data.readBigUInt64BE(i));
      if (v > 1_000 && v < 1e13) found.push(v);
    }
    if (found.length) {
      const balance = Math.min(...found) / 10 ** market.dec;
      positions.push({ sym: market.sym, dec: market.dec, balance, contractId });
    }
  }
  return positions;
}

async function getPositionDetail(address, network, poolId) {
  try {
    if (network === 'voi') {
      const positions = await decodeVoiBoxes(address, poolId);
      return positions.map(p => ({
        sym: p.sym,
        balance: p.balance,
        lt: LT_MAP[p.sym] || 0.70,
      }));
    }
  } catch (e) {
    console.error('getPositionDetail error:', e.message);
  }
  return [];
}

const __dir = path.dirname(fileURLToPath(import.meta.url));
const PORT = 8768;
const VOI_NODE = 'https://mainnet-api.voi.nodely.dev';
const ALGO_NODE = 'https://mainnet-api.4160.nodely.dev';
const BOT_WALLET = 'JV7URAS6XGXG7ZH44CWABWZYRIIJPXOWUVNFIJKLKJ3FRTADX2YWEJNO3A';

async function fetchExt(url) {
  const r = await fetch(url, { headers: { 'User-Agent': 'dorkfi-dashboard/1.0' } });
  return r.json();
}

async function getPositions() {
  const [voiAll, algoAll] = await Promise.all([
    fetchUserHealthAll('voi'),
    fetchUserHealthAll('algorand'),
  ]);

  const process = (rows, chainKey) => rows
    .filter(r => Number(r.totalBorrowValue) > 0)
    .map(r => ({
      userAddress: r.userAddress,
      appId: r.appId,
      healthFactor: r.healthFactor,
      totalCollateralValue: r.totalCollateralValue,
      totalBorrowValue: r.totalBorrowValue,
      network: chainKey,
      lastUpdated: r.lastUpdated,
    }));

  return [
    ...process(voiAll, 'voi'),
    ...process(algoAll, 'algorand'),
  ];
}

async function getLivePrices() {
  try {
    const [humbleRes, cgRes] = await Promise.all([
      fetchExt('https://humble-api.voi.nautilus.sh/prices?id=390001'),
      fetchExt('https://api.coingecko.com/api/v3/simple/price?ids=algorand&vs_currencies=usd'),
    ]);
    let voiUsd = 0.000182; // fallback
    for (const p of humbleRes.prices || []) {
      if (p.tokenId === '390001' && p.quoteTokenId === '395614') {
        voiUsd = Number(p.price) / 1e18;
        break;
      }
    }
    const algoUsd = cgRes?.algorand?.usd || 0.10;
    return { voiUsd, algoUsd };
  } catch {
    return { voiUsd: 0.000182, algoUsd: 0.10 };
  }
}

async function getBalances() {
  const [vd, ad, prices] = await Promise.all([
    fetchExt(`${VOI_NODE}/v2/accounts/${BOT_WALLET}`),
    fetchExt(`${ALGO_NODE}/v2/accounts/${BOT_WALLET}`),
    getLivePrices(),
  ]);
  const voiAssets = Object.fromEntries((vd.assets||[]).map(a=>[a['asset-id'],a.amount]));
  const algoAssets = Object.fromEntries((ad.assets||[]).map(a=>[a['asset-id'],a.amount]));
  const voi = vd.amount/1e6;
  const ausdc = (voiAssets[302190]||0)/1e6;
  const algo = ad.amount/1e6;
  const usdc = (algoAssets[31566704]||0)/1e6;
  return {
    voi, ausdc, algo, usdc,
    voiUsd: prices.voiUsd,
    algoUsd: prices.algoUsd,
    totalUsd: ausdc + usdc + (algo * prices.algoUsd) + (voi * prices.voiUsd),
  };
}

const server = http.createServer(async (req, res) => {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' };

  if (req.url === '/api/positions') {
    try {
      const data = await getPositions();
      res.writeHead(200, cors);
      res.end(JSON.stringify({ ok: true, data }));
    } catch(e) {
      res.writeHead(500, cors);
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  if (req.url.startsWith('/api/position-detail')) {
    try {
      const u = new URL(req.url, 'http://localhost');
      const address = u.searchParams.get('address');
      const network = u.searchParams.get('network') || 'voi';
      const poolId  = parseInt(u.searchParams.get('poolId') || '47139778');
      const detail  = await getPositionDetail(address, network, poolId);
      res.writeHead(200, cors);
      res.end(JSON.stringify({ ok: true, data: detail }));
    } catch(e) {
      res.writeHead(500, cors);
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  if (req.url === '/api/balances') {
    try {
      const data = await getBalances();
      res.writeHead(200, cors);
      res.end(JSON.stringify({ ok: true, data }));
    } catch(e) {
      res.writeHead(500, cors);
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // Serve static files
  const filePath = path.join(__dir, req.url === '/' ? 'index.html' : req.url);
  if (fs.existsSync(filePath)) {
    const ext = path.extname(filePath);
    const mime = { '.html':'text/html', '.js':'text/javascript', '.css':'text/css' }[ext] || 'text/plain';
    res.writeHead(200, { 'Content-Type': mime });
    fs.createReadStream(filePath).pipe(res);
  } else {
    res.writeHead(404); res.end('Not found');
  }
});

server.listen(PORT, () => console.log(`DorkFi Dashboard running on http://localhost:${PORT}`));
