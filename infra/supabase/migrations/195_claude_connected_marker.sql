-- 195_claude_connected_marker: when a machine's Claude account was connected
-- (US-52.3). The marker is the ONLY thing the factory stores about the
-- machine-held subscription credential — the token itself is written into the
-- agent slot env files on the machine by `claude setup-token` and never
-- transits the factory. It sits on agent_servers, not servers, because the
-- thing being connected is the agent host (the workdir whose slot envs carry
-- the token); a plain deploy target has nothing to connect.

alter table public.agent_servers
  add column if not exists claude_connected_at timestamptz;

comment on column public.agent_servers.claude_connected_at is
  'US-52.3: when the machine''s Claude subscription was last connected (token
   installed into the slot envs on the box). Null = not connected. The token
   itself never leaves the machine.';
