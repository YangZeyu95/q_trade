import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_ACCOUNT, normalizeAccount } from './dashboardData.js';

test('empty account responses retain render-safe defaults', () => {
  assert.deepEqual(normalizeAccount({}), DEFAULT_ACCOUNT);
  assert.deepEqual(normalizeAccount(null), DEFAULT_ACCOUNT);
});

test('account numbers are normalized before rendering', () => {
  const account = normalizeAccount({
    total_asset: '1234.5',
    cash: null,
    maintenance_margin_ratio: '0.42',
  });

  assert.equal(account.total_asset, 1234.5);
  assert.equal(account.cash, 0);
  assert.equal(account.market_value, 0);
  assert.equal(account.maintenance_margin_ratio, 0.42);
});
