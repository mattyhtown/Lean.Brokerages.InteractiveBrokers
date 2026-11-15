-- Adds Alpha Vantage's scored news to training_rows.

-- One row per symbol and article from Alpha Vantage's NEWS_SENTIMENT endpoint, with the
-- relevance and sentiment Alpha Vantage gave that symbol in that article. Rows are only
-- ever inserted, so an article keeps the scores it had when it was first loaded.
create table public.alphavantage_news (
  symbol text not null,
  url text not null,
  published_at timestamptz not null,
  title text not null,
  source text,
  relevance double precision not null,
  sentiment double precision not null,
  loaded_at timestamptz not null default now(),
  primary key (symbol, url)
);

alter table public.alphavantage_news enable row level security;

drop view public.training_rows;

-- One row per ticker and trading day that has a loaded close.
-- The session calendar is every date with a loaded close for any symbol, so a
-- symbol missing a bar gets a null return instead of a return to a later day.
-- A newsletter, news digest or Benzinga headline counts toward the first session
-- whose 16:00 New York close comes after it arrived, so features never use text
-- from after that close. Text with no session within 5 days is left out. Options
-- metrics are the ORATS row for the same session, which ORATS snapshots before
-- the close. An Alpha Vantage article is dated the same way from its publication
-- time; alphavantage_sentiment is Alpha Vantage's own score for the symbol,
-- averaged over the day's articles with each weighted by its relevance to the
-- symbol. market_news_sentiment is the index value for the day 21 days before
-- the session: the San Francisco Fed publishes about weekly and its newest day
-- can be 12 days old just before a release, so 21 leaves room for one missed
-- release.
-- `close` is the raw close. Forward returns are close to close, null until the
-- later closes are loaded, and adjusted for the splits and dividends in
-- price_adjustments. returns_adjusted is true when the symbol's factors are known
-- through the last close its returns use; otherwise a split or dividend inside
-- the return may be missing.
create view public.training_rows with (security_invoker = true) as
with sessions as (
  select
    trade_date,
    (trade_date + time '16:00') at time zone 'America/New_York' as close_at,
    row_number() over (order by trade_date) as session_number
  from (select distinct trade_date from public.daily_prices) loaded_dates
),
adjustment_rows as (
  select
    a.symbol,
    (r.factor ->> 'through_date')::date as through_date,
    (r.factor ->> 'price_factor')::numeric * (r.factor ->> 'split_factor')::numeric as scale
  from public.price_adjustments a
  cross join lateral jsonb_array_elements(a.factors) as r (factor)
),
prices as (
  select
    p.symbol,
    p.trade_date,
    p.close,
    s.session_number,
    p.close * coalesce(adjustment.scale, 1) as adjusted_close
  from public.daily_prices p
  join sessions s on s.trade_date = p.trade_date
  -- The factors of a date are those of the first entry dated on or after it.
  left join lateral (
    select r.scale
    from adjustment_rows r
    where r.symbol = p.symbol and r.through_date >= p.trade_date
    order by r.through_date
    limit 1
  ) adjustment on true
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
    avg(sentiment_positive - sentiment_negative) filter (where source = 'perplexity') as news_sentiment_net,
    count(*) filter (where source = 'benzinga') as benzinga_headline_count,
    avg(sentiment_positive) filter (where source = 'benzinga') as benzinga_sentiment_positive,
    avg(sentiment_neutral) filter (where source = 'benzinga') as benzinga_sentiment_neutral,
    avg(sentiment_negative) filter (where source = 'benzinga') as benzinga_sentiment_negative,
    avg(sentiment_positive - sentiment_negative) filter (where source = 'benzinga') as benzinga_sentiment_net
  from dated_mentions
  group by symbol, signal_date
),
scored_news as (
  select
    n.symbol,
    next_session.trade_date as signal_date,
    count(*) as article_count,
    sum(n.relevance * n.sentiment) / nullif(sum(n.relevance), 0) as sentiment,
    avg(n.relevance) as relevance
  from public.alphavantage_news n
  cross join lateral (
    select s.trade_date
    from sessions s
    where s.close_at > n.published_at
      and s.close_at <= n.published_at + interval '5 days'
    order by s.close_at
    limit 1
  ) next_session
  group by n.symbol, next_session.trade_date
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
  coalesce(s.benzinga_headline_count, 0) as benzinga_headline_count,
  s.benzinga_sentiment_positive,
  s.benzinga_sentiment_neutral,
  s.benzinga_sentiment_negative,
  s.benzinga_sentiment_net,
  coalesce(av.article_count, 0) as alphavantage_article_count,
  av.sentiment as alphavantage_sentiment,
  av.relevance as alphavantage_relevance,
  market.value as market_news_sentiment,
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
  next_1.adjusted_close / nullif(p.adjusted_close, 0) - 1 as forward_return_1d,
  next_5.adjusted_close / nullif(p.adjusted_close, 0) - 1 as forward_return_5d,
  coalesce(a.as_of >= coalesce(next_5.trade_date, next_1.trade_date), false) as returns_adjusted
from prices p
left join sentiment s on s.symbol = p.symbol and s.signal_date = p.trade_date
left join scored_news av on av.symbol = p.symbol and av.signal_date = p.trade_date
left join public.market_news_sentiment market on market.observation_date = p.trade_date - 21
left join public.options_daily o on o.symbol = p.symbol and o.trade_date = p.trade_date
left join public.price_adjustments a on a.symbol = p.symbol
left join prices next_1 on next_1.symbol = p.symbol and next_1.session_number = p.session_number + 1
left join prices next_5 on next_5.symbol = p.symbol and next_5.session_number = p.session_number + 5;

revoke all on public.alphavantage_news, public.training_rows from anon, authenticated;
grant select, insert on public.alphavantage_news to service_role;
grant select on public.training_rows to service_role;
