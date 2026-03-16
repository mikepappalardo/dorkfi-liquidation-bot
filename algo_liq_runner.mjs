/**
 * Algorand DorkFi Liquidation Runner
 * Called by liquidation_bot.py via subprocess
 * Usage: node algo_liq_runner.mjs <borrower> <collateralSymbol> <debtSymbol> <amount> <sender>
 */

import { prepareLiquidation } from '/Users/michaelpappalardo/DorkFiMCP/lib/builders.js';
import { getLiquidationCandidates } from '/Users/michaelpappalardo/DorkFiMCP/lib/liquidation.js';

const cmd = process.argv[2];

if (cmd === 'candidates') {
  const chain = process.argv[3] || 'algorand';
  try {
    const result = await getLiquidationCandidates(chain, { threshold: 1.0, limit: 50 });
    console.log(JSON.stringify(result));
  } catch (e) {
    console.error(JSON.stringify({ error: e.message }));
    process.exit(1);
  }
} else if (cmd === 'build') {
  const [, , , borrower, collateralSymbol, debtSymbol, amount, sender, chain] = process.argv;
  try {
    const result = await prepareLiquidation(chain || 'algorand', borrower, collateralSymbol, debtSymbol, parseFloat(amount), sender);
    // Return base64 encoded transactions
    const txns = result.transactions.map(t => Buffer.from(t).toString('base64'));
    console.log(JSON.stringify({ transactions: txns, details: result.details }));
  } catch (e) {
    console.error(JSON.stringify({ error: e.message }));
    process.exit(1);
  }
} else {
  console.error(JSON.stringify({ error: `Unknown command: ${cmd}` }));
  process.exit(1);
}
