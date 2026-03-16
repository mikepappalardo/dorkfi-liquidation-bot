import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { getLiquidationCandidates } from '/Users/michaelpappalardo/DorkFiMCP/lib/liquidation.js';
import { fetchUserHealthAll } from '/Users/michaelpappalardo/DorkFiMCP/lib/api.js';

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

async function getBalances() {
  const [vd, ad] = await Promise.all([
    fetchExt(`${VOI_NODE}/v2/accounts/${BOT_WALLET}`),
    fetchExt(`${ALGO_NODE}/v2/accounts/${BOT_WALLET}`),
  ]);
  const voiAssets = Object.fromEntries((vd.assets||[]).map(a=>[a['asset-id'],a.amount]));
  const algoAssets = Object.fromEntries((ad.assets||[]).map(a=>[a['asset-id'],a.amount]));
  return {
    voi: vd.amount/1e6,
    ausdc: (voiAssets[302190]||0)/1e6,
    algo: ad.amount/1e6,
    usdc: (algoAssets[31566704]||0)/1e6,
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
