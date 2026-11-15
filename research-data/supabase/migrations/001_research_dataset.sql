-- Research dataset: newsletters, FinBERT-scored passages, daily prices, and the
-- ticker x date training view built from them.
--
-- Row level security is on with no policies, so the Data API serves these tables
-- only to the service role (secret) key that the loader uses.

create table public.newsletters (
  id text primary key,
  sender text not null,
  publication text,
  subject text not null,
  -- When the mailbox received the message: the earliest moment its content was knowable.
  received_at timestamptz not null,
  body_text text not null,
  loaded_at timestamptz not null default now()
);

create index newsletters_received_at_idx on public.newsletters (received_at);

create table public.newsletter_passages (
  id bigint generated always as identity primary key,
  newsletter_id text not null references public.newsletters (id) on delete cascade,
  passage_index integer not null,
  passage_text text not null,
  tickers text[] not null,
  sentiment_label text,
  sentiment_positive real,
  sentiment_neutral real,
  sentiment_negative real,
  sentiment_model text,
  scored_at timestamptz,
  unique (newsletter_id, passage_index)
);

create index newsletter_passages_unscored_idx on public.newsletter_passages (id) where sentiment_label is null;

create table public.daily_prices (
  symbol text not null,
  trade_date date not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume bigint not null,
  dataset text not null,
  loaded_at timestamptz not null default now(),
  primary key (symbol, trade_date)
);

alter table public.newsletters enable row level security;
alter table public.newsletter_passages enable row level security;
alter table public.daily_prices enable row level security;

create view public.mentioned_tickers with (security_invoker = true) as
select distinct t.symbol
from public.newsletter_passages p
cross join lateral unnest(p.tickers) as t (symbol);

-- One row per ticker and trading day that has scored newsletter mentions.
-- A mention counts toward the first trading day whose 16:00 New York close comes
-- after the newsletter arrived, so features never use text from after that close.
-- Mentions with no loaded close within 5 days are left out instead of being
-- assigned to a later day. Forward returns are close to close and are null until
-- the later closes are loaded.
create view public.training_rows with (security_invoker = true) as
with price_returns as (
  select
    symbol,
    trade_date,
    close,
    lead(close, 1) over by_symbol / nullif(close, 0) - 1 as forward_return_1d,
    lead(close, 5) over by_symbol / nullif(close, 0) - 1 as forward_return_5d
  from public.daily_prices
  window by_symbol as (partition by symbol order by trade_date)
),
mentions as (
  select
    t.symbol,
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
  select m.*, next_close.trade_date as signal_date
  from mentions m
  cross join lateral (
    select min(d.trade_date) as trade_date
    from public.daily_prices d
    where d.symbol = m.symbol
      and (d.trade_date + time '16:00') at time zone 'America/New_York' > m.received_at
      and (d.trade_date + time '16:00') at time zone 'America/New_York' <= m.received_at + interval '5 days'
  ) next_close
  where next_close.trade_date is not null
)
select
  m.symbol,
  m.signal_date,
  count(*) as passage_count,
  count(distinct m.newsletter_id) as newsletter_count,
  avg(m.sentiment_positive) as sentiment_positive,
  avg(m.sentiment_neutral) as sentiment_neutral,
  avg(m.sentiment_negative) as sentiment_negative,
  avg(m.sentiment_positive - m.sentiment_negative) as sentiment_net,
  r.close,
  r.forward_return_1d,
  r.forward_return_5d
from dated_mentions m
join price_returns r on r.symbol = m.symbol and r.trade_date = m.signal_date
group by m.symbol, m.signal_date, r.close, r.forward_return_1d, r.forward_return_5d;

revoke all on public.newsletters, public.newsletter_passages, public.daily_prices,
  public.mentioned_tickers, public.training_rows from anon, authenticated;
grant select, insert, update on public.newsletters, public.newsletter_passages, public.daily_prices to service_role;
grant select on public.mentioned_tickers, public.training_rows to service_role;
