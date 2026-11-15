-- Keeps the record of the return model: how each retrained model scored on days it was
-- not trained on, what it predicted, and how those predictions turned out.

-- One row per retrain and horizon. Rows are only ever inserted.
-- oos_* is the model tested on history walked forward in time: trained on the rows
-- whose return was already known, it predicts the next 20 sessions and is trained
-- again. live_* is the same measures over the predictions in model_predictions whose
-- return has since become known; null until there are any.
-- features lists the training_rows columns the model was offered; one with no value
-- in any row it was trained on, such as a source not loaded yet, is not used.
--   rank_ic            mean over days of the rank correlation, across symbols, between
--                      the predictions and the returns; only days with 5 or more symbols
--   rank_ic_t          rank_ic against its own day-to-day spread, with days counted as
--                      rank_ic_days / horizon_days because longer returns overlap
--   hit_rate           share of predictions with the sign of the return
--   baseline_hit_rate  the same for always predicting the average training return
--   r2_vs_baseline     1 - the model's squared error / that of always predicting the
--                      average training return; above 0 beats it
--   top_minus_bottom   mean over days of the return of the third of symbols predicted
--                      highest minus the third predicted lowest
create table public.model_runs (
  id uuid primary key,
  trained_at timestamptz not null default now(),
  horizon_days smallint not null check (horizon_days > 0),
  model text not null,
  parameters jsonb not null,
  features jsonb not null,
  training_rows integer not null,
  training_sessions integer not null,
  first_signal_date date not null,
  last_signal_date date not null,
  -- Training returns that may miss a split or dividend (training_rows.returns_adjusted is false).
  unadjusted_returns integer not null,
  oos_predictions integer not null,
  oos_sessions integer not null,
  oos_first_signal_date date,
  oos_last_signal_date date,
  oos_rank_ic double precision,
  oos_rank_ic_days integer not null,
  oos_rank_ic_t double precision,
  oos_hit_rate double precision,
  oos_baseline_hit_rate double precision,
  oos_r2_vs_baseline double precision,
  oos_top_minus_bottom double precision,
  live_predictions integer not null,
  live_sessions integer not null,
  live_first_signal_date date,
  live_last_signal_date date,
  live_rank_ic double precision,
  live_rank_ic_days integer not null,
  live_rank_ic_t double precision,
  live_hit_rate double precision,
  live_baseline_hit_rate double precision,
  live_r2_vs_baseline double precision,
  live_top_minus_bottom double precision
);

-- What the model predicted for a symbol's return over the horizon_days sessions after
-- signal_date's close, made when that return was not yet known. Rows are only ever
-- inserted and the first prediction for a symbol, day and horizon is the one kept, so
-- a later run that could have seen more cannot replace it.
-- baseline_return is the average return of the rows the model was trained on.
create table public.model_predictions (
  symbol text not null,
  signal_date date not null,
  -- The horizons training_rows has a return for; model_prediction_outcomes looks them up by name.
  horizon_days smallint not null check (horizon_days in (1, 5)),
  run_id uuid not null references public.model_runs (id),
  predicted_return double precision not null,
  baseline_return double precision not null,
  created_at timestamptz not null default now(),
  primary key (symbol, signal_date, horizon_days)
);

create index model_predictions_run_id on public.model_predictions (run_id);

alter table public.model_runs enable row level security;
alter table public.model_predictions enable row level security;

-- Each stored prediction next to the return that followed, null until the later close
-- is loaded.
create view public.model_prediction_outcomes with (security_invoker = true) as
select
  p.symbol,
  p.signal_date,
  p.horizon_days,
  p.run_id,
  p.created_at,
  p.predicted_return,
  p.baseline_return,
  case p.horizon_days
    when 1 then t.forward_return_1d
    when 5 then t.forward_return_5d
  end as realized_return,
  t.returns_adjusted
from public.model_predictions p
left join public.training_rows t on t.symbol = p.symbol and t.signal_date = p.signal_date;

revoke all on public.model_runs, public.model_predictions, public.model_prediction_outcomes from anon, authenticated;
grant select, insert on public.model_runs, public.model_predictions to service_role;
grant select on public.model_prediction_outcomes to service_role;
