export const DEFAULT_ACCOUNT = Object.freeze({
  total_asset: 0,
  market_value: 0,
  cash: 0,
  buying_power: 0,
  total_pnl: 0,
  gross_leverage: 0,
  maintenance_margin_ratio: null,
  max_total_leverage: 2,
  min_maintenance_margin_ratio: 0.30,
  risk_status: 'unknown',
});

const NUMERIC_FIELDS = [
  'total_asset',
  'market_value',
  'cash',
  'buying_power',
  'total_pnl',
  'gross_leverage',
  'max_total_leverage',
  'min_maintenance_margin_ratio',
];

export const normalizeAccount = (value) => {
  const source = value && typeof value === 'object' && !Array.isArray(value)
    ? value
    : {};
  const account = { ...DEFAULT_ACCOUNT, ...source };

  for (const field of NUMERIC_FIELDS) {
    const number = Number(account[field]);
    account[field] = Number.isFinite(number) ? number : DEFAULT_ACCOUNT[field];
  }

  if (source.maintenance_margin_ratio == null) {
    account.maintenance_margin_ratio = null;
  } else {
    const marginRatio = Number(source.maintenance_margin_ratio);
    account.maintenance_margin_ratio = Number.isFinite(marginRatio) ? marginRatio : null;
  }

  return account;
};
