export const OPTION_MULTIPLIER = 100;

const finiteNumber = (value, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export function legPayoff(position, expirationPrice, spotPrice) {
  const price = Math.max(0, finiteNumber(expirationPrice));
  const quantity = Math.max(0, finiteNumber(position.quantity, 1));
  const direction = position.direction === 'short' ? -1 : 1;
  const fees = Math.max(0, finiteNumber(position.fees));
  if (position.type === 'stock') {
    const entry = finiteNumber(position.entryPrice, spotPrice);
    return direction * (price - entry) * quantity - fees;
  }

  const strike = finiteNumber(position.strike);
  const premium = finiteNumber(position.premium);
  const intrinsic = position.type === 'call'
    ? Math.max(price - strike, 0)
    : Math.max(strike - price, 0);
  return direction * (intrinsic - premium) * quantity * OPTION_MULTIPLIER - fees;
}

export function totalPayoff(positions, expirationPrice, spotPrice) {
  return positions.reduce(
    (total, position) => total + legPayoff(position, expirationPrice, spotPrice),
    0,
  );
}

export function netEntryCashFlow(positions) {
  return positions.reduce((total, position) => {
    const quantity = Math.max(0, finiteNumber(position.quantity, 1));
    const direction = position.direction === 'short' ? 1 : -1;
    const fees = Math.max(0, finiteNumber(position.fees));
    if (position.type === 'stock') {
      return total + direction * finiteNumber(position.entryPrice) * quantity - fees;
    }
    return total + direction * finiteNumber(position.premium) * quantity * OPTION_MULTIPLIER - fees;
  }, 0);
}

export function findBreakevens(positions, spotPrice) {
  if (!positions.length) return [];
  const strikes = positions
    .filter((position) => position.type !== 'stock')
    .map((position) => finiteNumber(position.strike))
    .filter((strike) => strike >= 0);
  const boundaries = [...new Set([0, ...strikes])].sort((a, b) => a - b);
  const roots = [];
  const addRoot = (value) => {
    if (value >= 0 && Number.isFinite(value) && !roots.some((root) => Math.abs(root - value) < 0.005)) {
      roots.push(value);
    }
  };

  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const left = boundaries[index];
    const right = boundaries[index + 1];
    const leftValue = totalPayoff(positions, left, spotPrice);
    const rightValue = totalPayoff(positions, right, spotPrice);
    if (Math.abs(leftValue) < 1e-8) addRoot(left);
    if (leftValue * rightValue < 0) {
      addRoot(left - (leftValue * (right - left)) / (rightValue - leftValue));
    }
  }

  const tailStart = boundaries.at(-1) || 0;
  const tailStep = Math.max(1, finiteNumber(spotPrice, 1), tailStart);
  const tailValue = totalPayoff(positions, tailStart, spotPrice);
  const nextValue = totalPayoff(positions, tailStart + tailStep, spotPrice);
  if (Math.abs(tailValue) < 1e-8) addRoot(tailStart);
  const tailSlope = (nextValue - tailValue) / tailStep;
  if (Math.abs(tailSlope) > 1e-10) {
    addRoot(tailStart - tailValue / tailSlope);
  }
  return roots.sort((a, b) => a - b);
}

export function payoffMetrics(positions, spotPrice) {
  if (!positions.length) {
    return { maxProfit: 0, maxLoss: 0, breakevens: [], currentPnl: 0 };
  }
  const strikes = positions
    .filter((position) => position.type !== 'stock')
    .map((position) => finiteNumber(position.strike));
  const points = [...new Set([0, ...strikes])];
  const values = points.map((price) => totalPayoff(positions, price, spotPrice));
  const tailStart = Math.max(0, ...points);
  const tailStep = Math.max(1, finiteNumber(spotPrice, 1), tailStart);
  const tailValue = totalPayoff(positions, tailStart, spotPrice);
  const tailSlope = (
    totalPayoff(positions, tailStart + tailStep, spotPrice) - tailValue
  ) / tailStep;
  return {
    maxProfit: tailSlope > 1e-10 ? Infinity : Math.max(...values),
    maxLoss: tailSlope < -1e-10 ? -Infinity : Math.min(...values),
    breakevens: findBreakevens(positions, spotPrice),
    currentPnl: totalPayoff(positions, spotPrice, spotPrice),
  };
}

export function buildPayoffCurve(positions, spotPrice, pointCount = 241) {
  const spot = Math.max(0.01, finiteNumber(spotPrice, 100));
  const strikes = positions
    .filter((position) => position.type !== 'stock')
    .map((position) => finiteNumber(position.strike));
  const minPrice = 0;
  const maxPrice = Math.max(spot * 1.6, ...strikes.map((strike) => strike * 1.25), 1);
  const evenlySpaced = Array.from({ length: Math.max(2, pointCount) }, (_, index) => (
    minPrice + (maxPrice - minPrice) * index / (Math.max(2, pointCount) - 1)
  ));
  // Expiration payoff is piecewise linear. Including every kink and zero crossing
  // keeps the rendered line exact instead of smoothing over a narrow spread.
  const criticalPrices = [minPrice, maxPrice, spot, ...strikes, ...findBreakevens(positions, spot)];
  const prices = [...new Set([...evenlySpaced, ...criticalPrices]
    .filter((price) => Number.isFinite(price) && price >= minPrice && price <= maxPrice))]
    .sort((a, b) => a - b);
  return prices.map((price) => ({ price, pnl: totalPayoff(positions, price, spot) }));
}

const normalizeContracts = (contracts, type) => {
  const byStrike = new Map();
  for (const contract of contracts || []) {
    const strike = finiteNumber(contract?.strike, -1);
    const hasQuote = ['bid', 'ask', 'mid', 'last'].some((field) => finiteNumber(contract?.[field]) > 0);
    if (strike <= 0 || !hasQuote) continue;
    const normalized = { ...contract, strike, option_type: contract.option_type || type };
    const existing = byStrike.get(strike);
    const quoteQuality = Number(normalized.bid > 0) + Number(normalized.ask > 0);
    const existingQuality = Number(existing?.bid > 0) + Number(existing?.ask > 0);
    if (!existing || quoteQuality > existingQuality) byStrike.set(strike, normalized);
  }
  return [...byStrike.values()].sort((left, right) => left.strike - right.strike);
};

const closestToStrike = (contracts, strike) => contracts.reduce((best, contract) => (
  !best || Math.abs(contract.strike - strike) < Math.abs(best.strike - strike) ? contract : best
), null);

const hasUsableDelta = (contract) => {
  const delta = Math.abs(finiteNumber(contract?.delta));
  return delta > 0.001 && delta <= 1.001;
};

const selectionScore = (contract, targetDelta, targetStrike, spot) => (
  hasUsableDelta(contract)
    ? Math.abs(Math.abs(finiteNumber(contract.delta)) - targetDelta)
    : Math.abs(contract.strike - targetStrike) / Math.max(spot, 0.01)
);

const bestContract = (contracts, targetDelta, targetStrike, spot) => contracts.reduce((best, contract) => {
  const score = selectionScore(contract, targetDelta, targetStrike, spot);
  if (!best || score < best.score - 1e-12) return { contract, score };
  if (Math.abs(score - best.score) < 1e-12
    && Math.abs(contract.strike - targetStrike) < Math.abs(best.contract.strike - targetStrike)) {
    return { contract, score };
  }
  return best;
}, null)?.contract || null;

const optionLeg = (contract, direction, quantity = 1) => ({
  type: contract.option_type,
  direction,
  strike: contract.strike,
  quantity,
  contract,
});

const equalWidthCondor = (puts, calls, spot) => {
  let best = null;
  const shortPuts = puts.filter((contract) => contract.strike < spot);
  const shortCalls = calls.filter((contract) => contract.strike > spot);
  const strikeKey = (strike) => strike.toFixed(6);
  const callsByStrike = new Map(calls.map((contract) => [strikeKey(contract.strike), contract]));
  for (const shortPut of shortPuts) {
    for (const longPut of puts.filter((contract) => contract.strike < shortPut.strike)) {
      const putWidth = shortPut.strike - longPut.strike;
      for (const shortCall of shortCalls) {
        if (shortPut.strike >= shortCall.strike) continue;
        const longCall = callsByStrike.get(strikeKey(shortCall.strike + putWidth));
        if (!longCall) continue;
        const score = selectionScore(shortPut, 0.20, spot * 0.95, spot)
          + selectionScore(shortCall, 0.20, spot * 1.05, spot)
          + putWidth / spot * 0.05;
        if (!best || score < best.score) {
          best = { longPut, shortPut, shortCall, longCall, score };
        }
      }
    }
  }
  return best;
};

const equalWidthButterfly = (calls, spot) => {
  let best = null;
  for (let middleIndex = 1; middleIndex < calls.length - 1; middleIndex += 1) {
    const middle = calls[middleIndex];
    for (const lower of calls.slice(0, middleIndex)) {
      const width = middle.strike - lower.strike;
      const upper = calls.find((contract) => Math.abs(contract.strike - (middle.strike + width)) < 0.001);
      if (!upper) continue;
      const score = Math.abs(middle.strike - spot) / spot + width / spot * 0.01;
      if (!best || score < best.score) best = { lower, middle, upper, score };
    }
  }
  return best;
};

/**
 * Build a strategy from contracts that actually exist in the selected HS chain.
 * Delta is preferred for OTM selection; moneyness is the deterministic fallback
 * when the feed does not provide a usable delta.
 */
export function strategyPositions(name, spotPrice, chain = {}) {
  const spot = Math.max(0.01, finiteNumber(spotPrice, 100));
  const calls = normalizeContracts(chain.calls, 'call');
  const puts = normalizeContracts(chain.puts, 'put');
  const stock = { type: 'stock', direction: 'long', quantity: 100, entryPrice: spot };
  const otmCall = bestContract(calls.filter((contract) => contract.strike > spot), 0.30, spot * 1.03, spot);
  const otmPut = bestContract(puts.filter((contract) => contract.strike < spot), 0.30, spot * 0.97, spot);
  const atmCall = closestToStrike(calls, spot);
  const atmPut = closestToStrike(puts, spot);

  if (name === 'covered_call' && otmCall) return [stock, optionLeg(otmCall, 'short')];
  if (name === 'protective_put' && otmPut) return [stock, optionLeg(otmPut, 'long')];
  if (name === 'collar' && otmPut && otmCall) {
    return [stock, optionLeg(otmPut, 'long'), optionLeg(otmCall, 'short')];
  }
  if (name === 'bull_call' && atmCall) {
    const shortCall = bestContract(calls.filter((contract) => contract.strike > atmCall.strike), 0.30, spot * 1.03, spot);
    if (shortCall) return [optionLeg(atmCall, 'long'), optionLeg(shortCall, 'short')];
  }
  if (name === 'bear_put' && atmPut) {
    const shortPut = bestContract(puts.filter((contract) => contract.strike < atmPut.strike), 0.30, spot * 0.97, spot);
    if (shortPut) return [optionLeg(atmPut, 'long'), optionLeg(shortPut, 'short')];
  }
  if (name === 'straddle') {
    const commonStrikes = calls
      .map((contract) => contract.strike)
      .filter((strike) => puts.some((contract) => contract.strike === strike));
    const strike = commonStrikes.reduce((best, candidate) => (
      best === null || Math.abs(candidate - spot) < Math.abs(best - spot) ? candidate : best
    ), null);
    if (strike !== null) {
      return [
        optionLeg(calls.find((contract) => contract.strike === strike), 'long'),
        optionLeg(puts.find((contract) => contract.strike === strike), 'long'),
      ];
    }
  }
  if (name === 'strangle') {
    const call = bestContract(calls.filter((contract) => contract.strike > spot), 0.25, spot * 1.04, spot);
    const put = bestContract(puts.filter((contract) => contract.strike < spot), 0.25, spot * 0.96, spot);
    if (call && put) return [optionLeg(call, 'long'), optionLeg(put, 'long')];
  }
  if (name === 'iron_condor') {
    const condor = equalWidthCondor(puts, calls, spot);
    if (condor) return [
      optionLeg(condor.longPut, 'long'), optionLeg(condor.shortPut, 'short'),
      optionLeg(condor.shortCall, 'short'), optionLeg(condor.longCall, 'long'),
    ];
  }
  if (name === 'butterfly') {
    const butterfly = equalWidthButterfly(calls, spot);
    if (butterfly) return [
      optionLeg(butterfly.lower, 'long'), optionLeg(butterfly.middle, 'short', 2),
      optionLeg(butterfly.upper, 'long'),
    ];
  }
  return [];
}
