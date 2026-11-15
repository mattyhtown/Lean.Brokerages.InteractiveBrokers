-- Adds ORATS daily options metrics and Perplexity news digests, and rebuilds the
-- training view on a trading-session calendar.

-- News digests share the newsletter tables; `source` tells them apart.
alter table public.newsletters add column source text not null default 'gmail';

create table public.options_daily (
  symbol text not null,
  trade_date date not null,
  iv_30d double precision,
  iv_percentile_1y double precision,
  hv_20d double precision,
  skew_slope double precision,
  contango double precision,
  call_volume bigint,
  put_volume bigint,
  call_open_interest bigint,
  put_open_interest bigint,
  -- When ORATS last wrote the row.
  source_updated_at timestamptz,
  loaded_at timestamptz not null default now(),
  primary key (symbol, trade_date)
);

alter table public.options_daily enable row level security;

drop view public.training_rows;

-- One row per ticker and trading day that has a loaded close.
-- The session calendar is every date with a loaded close for any symbol, so a
-- symbol missing a bar gets a null return instead of a return to a later day.
-- A newsletter or news digest counts toward the first session whose 16:00 New
-- York close comes after it arrived, so features never use text from after that
-- close. Text with no session within 5 days is left out. Options metrics are the
-- ORATS row for the same session, which ORATS snapshots before the close.
-- Forward returns are close to close and are null until the later closes are loaded.
create view public.training_rows with (security_invoker = true) as
with sessions as (
  select
    trade_date,
    (trade_date + time '16:00') at time zone 'America/New_York' as close_at,
    row_number() over (order by trade_date) as session_number
  from (select distinct trade_date from public.daily_prices) loaded_dates
),
prices as (
  select p.symbol, p.trade_date, p.close, s.session_number
  from public.daily_prices p
  join sessions s on s.trade_date = p.trade_date
),
mentions as (
  select
    t.symbol,
    n.source,
    p.newsletter_id,
    n.received_at,
    p.sentiment_positive,
    p.sentiment_neutral,
    p.sentiment_negative
  from public.newsletter_passages p
  join public.newsletters n on n.id = p.newsletter_id
  cross join lateral unnest(p.tickers) as t (symbol)
  where p.sentiment_label is not null
),
dated_mentions as (
  select m.*, next_session.trade_date as signal_date
  from mentions m
  cross join lateral (
    select s.trade_date
    from sessions s
    where s.close_at > m.received_at
      and s.close_at <= m.received_at + interval '5 days'
    order by s.close_at
    limit 1
  ) next_session
),
sentiment as (
  select
    symbol,
    signal_date,
    count(*) filter (where source = 'gmail') as passage_count,
    count(distinct newsletter_id) filter (where source = 'gmail') as newsletter_count,
    avg(sentiment_positive) filter (where source = 'gmail') as sentiment_positive,
    avg(sentiment_neutral) filter (where source = 'gmail') as sentiment_neutral,
    avg(sentiment_negative) filter (where source = 'gmail') as sentiment_negative,
    avg(sentiment_positive - sentiment_negative) filter (where source = 'gmail') as sentiment_net,
    count(*) filter (where source = 'perplexity') as news_item_count,
    avg(sentiment_positive) filter (where source = 'perplexity') as news_sentiment_positive,
    avg(sentiment_neutral) filter (where source = 'perplexity') as news_sentiment_neutral,
    avg(sentiment_negative) filter (where source = 'perplexity') as news_sentiment_negative,
    avg(sentiment_positive - sentiment_negative) filter (where source = 'perplexity') as news_sentiment_net
  from dated_mentions
  group by symbol, signal_date
)
select
  p.symbol,
  p.trade_date as signal_date,
  coalesce(s.passage_count, 0) as passage_count,
  coalesce(s.newsletter_count, 0) as newsletter_count,
  s.sentiment_positive,
  s.sentiment_neutral,
  s.sentiment_negative,
  s.sentiment_net,
  coalesce(s.news_item_count, 0) as news_item_count,
  s.news_sentiment_positive,
  s.news_sentiment_neutral,
  s.news_sentiment_negative,
  s.news_sentiment_net,
  o.iv_30d,
  o.iv_percentile_1y,
  o.hv_20d,
  o.skew_slope,
  o.contango,
  o.call_volume,
  o.put_volume,
  o.call_open_interest,
  o.put_open_interest,
  p.close,
  next_1.close / nullif(p.close, 0) - 1 as forward_return_1d,
  next_5.close / nullif(p.close, 0) - 1 as forward_return_5d
from prices p
left join sentiment s on s.symbol = p.symbol and s.signal_date = p.trade_date
left join public.options_daily o on o.symbol = p.symbol and o.trade_date = p.trade_date
left join prices next_1 on next_1.symbol = p.symbol and next_1.session_number = p.session_number + 1
left join prices next_5 on next_5.symbol = p.symbol and next_5.session_number = p.session_number + 5;

revoke all on public.options_daily, public.training_rows from anon, authenticated;
grant select, insert, update on public.options_daily to service_role;
grant select on public.training_rows to service_role;
