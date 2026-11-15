// Names only. Values are set per deployment with `agent-sdk secret set`.
export const SECRET_NAMES = [
  "UNUSUAL_WHALES_API_KEY",
  "ORATS_API_TOKEN",
  "ALPACA_API_KEY",
  "ALPACA_API_SECRET",
  "THETADATA_USERNAME",
  "THETADATA_PASSWORD",
  "TRADING_ECONOMICS_API_KEY",
  "QUANTCONNECT_USER_ID",
  "QUANTCONNECT_API_TOKEN",
] as const;

// Hosts the engine code on this branch calls. Theta Data, Trading Economics
// and QuantConnect have no client code here yet, so their hosts are not listed.
export const EGRESS_DOMAINS = [
  "api.unusualwhales.com",
  "data.alpaca.markets",
  "api.orats.io",
];
