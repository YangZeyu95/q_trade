import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPayoffCurve,
  findBreakevens,
  legPayoff,
  netEntryCashFlow,
  payoffMetrics,
  strategyPositions,
  totalPayoff,
} from './optionMath.js';


test('long call payoff uses the US option 100-share multiplier', () => {
  const call = { type: 'call', direction: 'long', strike: 100, premium: 5, quantity: 2 };
  assert.equal(legPayoff(call, 120, 100), 3000);
  assert.equal(legPayoff(call, 90, 100), -1000);
});

test('covered call reports capped profit and downside breakeven', () => {
  const positions = [
    { type: 'stock', direction: 'long', quantity: 100, entryPrice: 100 },
    { type: 'call', direction: 'short', strike: 110, premium: 3, quantity: 1 },
  ];
  const metrics = payoffMetrics(positions, 100);
  assert.equal(metrics.maxProfit, 1300);
  assert.equal(metrics.maxLoss, -9700);
  assert.deepEqual(metrics.breakevens.map((value) => Number(value.toFixed(2))), [97]);
});

test('long straddle has two exact breakevens and unlimited upside', () => {
  const positions = [
    { type: 'call', direction: 'long', strike: 100, premium: 5, quantity: 1 },
    { type: 'put', direction: 'long', strike: 100, premium: 4, quantity: 1 },
  ];
  assert.deepEqual(findBreakevens(positions, 100), [91, 109]);
  assert.equal(payoffMetrics(positions, 100).maxProfit, Infinity);
  assert.equal(totalPayoff(positions, 100, 100), -900);
});

test('strategy templates create valid multi-leg positions around spot', () => {
  const contract = (optionType, strike, delta) => ({
    option_type: optionType,
    strike,
    delta,
    bid: 1,
    ask: 1.2,
    last: 1.1,
    contract_symbol: `${optionType}-${strike}`,
  });
  const chain = {
    calls: [
      contract('call', 85, 0.90), contract('call', 90, 0.80),
      contract('call', 95, 0.65), contract('call', 100, 0.52),
      contract('call', 105, 0.30), contract('call', 110, 0.18),
      contract('call', 115, 0.10),
    ],
    puts: [
      contract('put', 85, -0.08), contract('put', 90, -0.18),
      contract('put', 95, -0.30), contract('put', 100, -0.48),
      contract('put', 105, -0.65), contract('put', 110, -0.80),
      contract('put', 115, -0.90),
    ],
  };

  const expectedLengths = {
    covered_call: 2, protective_put: 2, bull_call: 2, bear_put: 2,
    straddle: 2, strangle: 2, iron_condor: 4, butterfly: 3, collar: 3,
  };
  for (const [name, expectedLength] of Object.entries(expectedLengths)) {
    assert.equal(strategyPositions(name, 101, chain).length, expectedLength, name);
  }

  const straddle = strategyPositions('straddle', 101, chain);
  assert.deepEqual(straddle.map((position) => position.strike), [100, 100]);

  const condor = strategyPositions('iron_condor', 101, chain);
  assert.deepEqual(condor.map((position) => position.direction), ['long', 'short', 'short', 'long']);
  assert.deepEqual(condor.map((position) => position.strike), [85, 90, 110, 115]);
  assert.equal(condor[1].strike - condor[0].strike, condor[3].strike - condor[2].strike);

  const butterfly = strategyPositions('butterfly', 101, chain);
  assert.deepEqual(butterfly.map((position) => position.strike), [95, 100, 105]);
  assert.deepEqual(butterfly.map((position) => position.quantity), [1, 2, 1]);
});

test('fees reduce payoff and entry cash flow keeps credit/debit direction', () => {
  const positions = [
    { type: 'call', direction: 'long', strike: 100, premium: 2, quantity: 1, fees: 0.65 },
    { type: 'call', direction: 'short', strike: 110, premium: 1, quantity: 1, fees: 0.65 },
  ];
  assert.equal(Number(netEntryCashFlow(positions).toFixed(2)), -101.3);
  assert.equal(Number(totalPayoff(positions, 90, 100).toFixed(2)), -101.3);
});

test('standard strategy payoff metrics match their analytical results', () => {
  const cases = [
    {
      name: 'protective put',
      positions: [
        { type: 'stock', direction: 'long', entryPrice: 100, quantity: 100 },
        { type: 'put', direction: 'long', strike: 95, premium: 2, quantity: 1 },
      ],
      maxProfit: Infinity, maxLoss: -700, breakevens: [102],
    },
    {
      name: 'bull call spread',
      positions: [
        { type: 'call', direction: 'long', strike: 100, premium: 6, quantity: 1 },
        { type: 'call', direction: 'short', strike: 110, premium: 2, quantity: 1 },
      ],
      maxProfit: 600, maxLoss: -400, breakevens: [104],
    },
    {
      name: 'bear put spread',
      positions: [
        { type: 'put', direction: 'long', strike: 110, premium: 8, quantity: 1 },
        { type: 'put', direction: 'short', strike: 100, premium: 3, quantity: 1 },
      ],
      maxProfit: 500, maxLoss: -500, breakevens: [105],
    },
    {
      name: 'long strangle',
      positions: [
        { type: 'put', direction: 'long', strike: 95, premium: 3, quantity: 1 },
        { type: 'call', direction: 'long', strike: 105, premium: 2, quantity: 1 },
      ],
      maxProfit: Infinity, maxLoss: -500, breakevens: [90, 110],
    },
    {
      name: 'iron condor',
      positions: [
        { type: 'put', direction: 'long', strike: 90, premium: 1, quantity: 1 },
        { type: 'put', direction: 'short', strike: 95, premium: 2, quantity: 1 },
        { type: 'call', direction: 'short', strike: 105, premium: 2, quantity: 1 },
        { type: 'call', direction: 'long', strike: 110, premium: 1, quantity: 1 },
      ],
      maxProfit: 200, maxLoss: -300, breakevens: [93, 107],
    },
    {
      name: 'call butterfly',
      positions: [
        { type: 'call', direction: 'long', strike: 90, premium: 12, quantity: 1 },
        { type: 'call', direction: 'short', strike: 100, premium: 6, quantity: 2 },
        { type: 'call', direction: 'long', strike: 110, premium: 2, quantity: 1 },
      ],
      maxProfit: 800, maxLoss: -200, breakevens: [92, 108],
    },
    {
      name: 'collar',
      positions: [
        { type: 'stock', direction: 'long', entryPrice: 100, quantity: 100 },
        { type: 'put', direction: 'long', strike: 95, premium: 2, quantity: 1 },
        { type: 'call', direction: 'short', strike: 105, premium: 2, quantity: 1 },
      ],
      maxProfit: 500, maxLoss: -500, breakevens: [100],
    },
  ];

  for (const item of cases) {
    const metrics = payoffMetrics(item.positions, 100);
    assert.equal(metrics.maxProfit, item.maxProfit, `${item.name} max profit`);
    assert.equal(metrics.maxLoss, item.maxLoss, `${item.name} max loss`);
    assert.deepEqual(metrics.breakevens, item.breakevens, `${item.name} breakevens`);
  }
});

test('payoff curve contains exact strikes, spot and breakevens', () => {
  const positions = [
    { type: 'call', direction: 'long', strike: 100, premium: 0.05, quantity: 1 },
    { type: 'call', direction: 'short', strike: 100.1, premium: 0.01, quantity: 1 },
  ];
  const curve = buildPayoffCurve(positions, 100.03, 5);
  const prices = curve.map((point) => point.price);
  for (const criticalPrice of [100, 100.03, 100.04, 100.1]) {
    assert.ok(prices.some((price) => Math.abs(price - criticalPrice) < 1e-8), `${criticalPrice}`);
  }
  assert.ok(Math.abs(curve.find((point) => Math.abs(point.price - 100.04) < 1e-8).pnl) < 1e-8);
});
