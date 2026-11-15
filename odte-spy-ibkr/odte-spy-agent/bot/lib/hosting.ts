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

// Every outbound host the exposed engine jobs call. Theta Data is absent on
// purpose: its client talks to a Theta Terminal on 127.0.0.1, which a hosted
// agent does not have.
export const EGRESS_DOMAINS = [
  "api.unusualwhales.com",
  "data.alpaca.markets",
  "api.orats.io",
  "api.tradingeconomics.com",
  "www.quantconnect.com",
];
