-- A second bot's journal: lets that bot add trades to this one journal, and do nothing else.
-- Run once in the Supabase SQL editor, after journal_bot_access.sql (which made the table and the function).
-- Replace USER_ID, ACCOUNT and TOKEN_SHA256 first; the bot keeps the token itself.
begin;
insert into public.journals (id, user_id, data) values ('bot-stratbot-15m-hs4h-demo', 'USER_ID', jsonb_build_object(
  'id','bot-stratbot-15m-hs4h-demo', 'name','StratBot 15M Hammer/Shooters 4H Demo', 'jtype','Demo', 'account','ACCOUNT',
  'instrument','MNQ', 'pointValue',2, 'tickSize',0.25, 'contracts',1, 'direction','Long and Short',
  'signals', jsonb_build_array('r322','r122','r22'),
  'entryTF','15m', 'htf', jsonb_build_array('30m','1h','4h'), 'htf4','D', 'keyTF','30m', 'ftfc','Full FTFC only (A+)',
  'exitStyle','Trail from entry', 'stopPlacement','Setup candle (C1)', 'stopBuf',1, 'tp1Pct',50, 'minRR1',1,
  'hours','0800-1640', 'maxTrades',0, 'maxLosses',0,
  'notes','Logged automatically by the second trading bot on its own Tradovate demo account: 15m Hammer/Shooter reversals, Expansion with no flip, NY 8:00 to 4:40, Success Protocol on 4h (the stop trails the last finished 4h candle). P&L is after Tradovate fees.',
  'created', (extract(epoch from now()) * 1000)::bigint));
insert into public.bot_log_access (journal_id, user_id, token_hash)
values ('bot-stratbot-15m-hs4h-demo', 'USER_ID', 'TOKEN_SHA256');
commit;
