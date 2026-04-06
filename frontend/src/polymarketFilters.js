/**
 * Keep matrix / leaderboard / reasoning rows that represent a Polymarket market
 * (question text is the primary display identity).
 */
export function isPolymarketMarketRow(row) {
  if (!row || typeof row !== 'object') return false;
  const q = String(row.polymarket_question ?? '').trim();
  return q.length > 0;
}
