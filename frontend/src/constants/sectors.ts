/**
 * Static ticker → sector map for the CSP capital allocator's per-sector
 * concentration cap. Sourced from the curated universe groupings in
 * backend/services/universe.py (single source of truth for tickers).
 *
 * Coarse, assignment-risk-oriented buckets: names that tend to sell off
 * together (same macro/rate/commodity driver) share a bucket, so the
 * per-sector cap limits correlated tail exposure — not GICS precision.
 *
 * Unknown tickers (e.g. arbitrary custom runs) fall back to the symbol
 * itself as its own sector, so they are never artificially lumped together.
 */

const SECTOR_MAP: Record<string, string> = {}

function assign(sector: string, symbols: string[]): void {
  for (const s of symbols) SECTOR_MAP[s] = sector
}

assign('Semiconductors', [
  'NVDA', 'AMD', 'AVGO', 'QCOM', 'MRVL', 'ARM', 'MU', 'INTC', 'TSM', 'ASML',
  'AMAT', 'LRCX', 'KLAC', 'TXN', 'ON', 'MPWR', 'NXPI', 'ADI', 'MCHP', 'WOLF',
  'ALAB', 'CRDO', 'COHR', 'LITE',
])
assign('Mega-cap Tech', [
  'MSFT', 'GOOGL', 'GOOG', 'META', 'AMZN', 'AAPL', 'TSLA', 'BIDU', 'BABA',
  'NFLX', 'DIS',
])
assign('Software', [
  'PLTR', 'CRWD', 'NET', 'SNOW', 'DDOG', 'ZS', 'PANW', 'NOW', 'CRM', 'ORCL',
  'WDAY', 'HUBS', 'MDB', 'APP', 'GTLB', 'CFLT', 'ADBE', 'INTU', 'TEAM', 'DUOL',
  'S', 'BILL', 'AI', 'SOUN', 'RDDT', 'RBLX', 'SHOP', 'PATH', 'SPOT', 'ROKU',
])
assign('Tech Infrastructure', [
  'SMCI', 'DELL', 'HPE', 'IBM', 'CSCO', 'ANET', 'JNPR', 'CIEN', 'NTAP', 'PSTG',
  'EQIX', 'DLR', 'IRM', 'NBIS', 'CRWV', 'IREN',
])
assign('Fintech & Crypto', [
  'COIN', 'HOOD', 'SQ', 'AFRM', 'SOFI', 'MSTR', 'PYPL',
  'RIOT', 'MARA', 'HIVE', 'WULF', 'CLSK', 'BTDR', 'APLD', 'GLXY',
])
assign('Energy & Power', [
  'VST', 'CEG', 'NRG', 'TLN', 'NEE', 'ETR', 'DUK', 'SO', 'EXC', 'OKLO', 'SMR',
  'BWXT', 'CCJ', 'PWR', 'FSLR', 'GEV', 'ETN', 'VRT',
  'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'OXY', 'MPC',
])
assign('Financials', [
  'JPM', 'BAC', 'GS', 'MS', 'V', 'MA', 'SCHW', 'BLK', 'WFC', 'C', 'AXP',
])
assign('Consumer Staples', ['KO', 'PG', 'PEP', 'COST', 'WMT', 'MO', 'MDLZ'])
assign('Healthcare', [
  'JNJ', 'UNH', 'ABT', 'MRK', 'PFE', 'ABBV', 'TMO', 'LLY', 'MRNA', 'HIMS',
  'ISRG', 'DXCM', 'VRTX', 'REGN', 'BMY', 'RXRX',
])
assign('Industrials', [
  'CAT', 'DE', 'HON', 'RTX', 'LMT', 'UNP', 'GE', 'NOC', 'BA', 'URI',
])
assign('Materials', ['LIN', 'FCX', 'NEM', 'NUE', 'AA', 'X', 'CLF'])
assign('Real Estate', ['PLD', 'AMT', 'SPG', 'O'])
assign('Consumer Discretionary', [
  'HD', 'LOW', 'NKE', 'MCD', 'TGT', 'LULU', 'SBUX', 'CMG', 'ABNB', 'UBER',
  'DASH', 'BKNG', 'BROS', 'RIVN', 'LCID', 'NIO', 'LI', 'XPEV', 'PDD', 'JD',
  'DAL', 'UAL', 'LUV', 'CCL', 'RCL',
])
assign('Quantum & Space', [
  'IONQ', 'RGTI', 'ACHR', 'RKLB', 'JOBY', 'QBTS', 'ASTS', 'BBAI', 'SERV', 'AUR', 'TEM',
])
assign('ETF', ['QQQ', 'SPY', 'IWM', 'SOXX', 'SMH', 'XLE', 'XLF', 'XLK', 'XLV'])

/**
 * Resolve a ticker to its concentration bucket. Unknown tickers map to
 * themselves so they are treated as their own sector (no false grouping).
 */
export function getSector(symbol: string): string {
  return SECTOR_MAP[symbol.toUpperCase()] ?? symbol.toUpperCase()
}
