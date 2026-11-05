-- Lets the bot add trades to one Strat Trading Journal journal, and do nothing else.
-- Run once in the Supabase SQL editor. Replace USER_ID and TOKEN_SHA256 first; the bot keeps the token itself.
begin;

create table public.bot_log_access (
  journal_id text primary key references public.journals(id) on delete cascade,
  user_id uuid not null,
  token_hash text not null unique,
  created_at timestamptz not null default now()
);
alter table public.bot_log_access enable row level security;  -- no policies, so nothing can read it through the API
revoke all on public.bot_log_access from anon, authenticated;

insert into public.journals (id, user_id, data) values ('bot-stratbot-1m-demo', 'USER_ID', jsonb_build_object(
  'id','bot-stratbot-1m-demo', 'name','StratBot 1M Demo', 'jtype','Demo', 'account','DEMO728949',
  'instrument','MNQ', 'pointValue',2, 'tickSize',0.25, 'contracts',1, 'direction','Long and Short',
  'signals', jsonb_build_array('f322','f122','f22','r312','r212','mibr','c312','c212','mibc','r22','r322','r122','r32','n32r','c22','nirv','c32','mg'),
  'entryTF','1m', 'htf', jsonb_build_array('5m','15m','30m'), 'htf4','1h', 'keyTF','15m', 'ftfc','Full FTFC only (A+)',
  'exitStyle','Trail from entry', 'stopPlacement','Setup candle (C1)', 'stopBuf',1, 'tp1Pct',50, 'minRR1',1,
  'hours','', 'maxTrades',0, 'maxLosses',0,
  'notes','Logged automatically by the trading bot on the Tradovate demo account. P&L is after Tradovate fees.',
  'created', (extract(epoch from now()) * 1000)::bigint));

insert into public.bot_log_access (journal_id, user_id, token_hash)
values ('bot-stratbot-1m-demo', 'USER_ID', 'TOKEN_SHA256');

create function public.bot_log_trade(p_token text, p_trade jsonb) returns text
language plpgsql security definer set search_path = '' as $$
declare
  a public.bot_log_access;
  tid text := p_trade->>'id';
  tdate text := p_trade->>'date';
begin
  select * into a from public.bot_log_access
   where token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex');
  if not found then raise exception 'not allowed'; end if;
  if tid is null or tid !~ '^bot-[A-Za-z0-9_-]{1,64}$' then raise exception 'bad trade id'; end if;
  if tdate is null or tdate !~ '^\d{4}-\d{2}-\d{2}$' then raise exception 'bad trade date'; end if;
  if octet_length(p_trade::text) > 20000 then raise exception 'trade too large'; end if;
  -- "do nothing" means a resend cannot change or duplicate a trade that is already there
  insert into public.trades (id, user_id, journal_id, trade_date, data)
  values (tid, a.user_id, a.journal_id, tdate::date, p_trade || jsonb_build_object('id', tid, 'jid', a.journal_id))
  on conflict (id) do nothing;
  return tid;
end $$;
revoke all on function public.bot_log_trade(text, jsonb) from public, anon, authenticated;
grant execute on function public.bot_log_trade(text, jsonb) to anon;

commit;
