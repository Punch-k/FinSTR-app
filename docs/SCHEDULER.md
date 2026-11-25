# Reliable refresh schedule (3 times per trading day)

FinSTR's data should refresh at about **10:30 AM, 1:00 PM and 4:20 PM New York time** on trading days.

## Why GitHub's schedule alone is not enough

`.github/workflows/deploy.yml` used to rely on four `schedule:` crons. In November 2026 GitHub started
those runs 7 to 11 hours late every day (e.g. the 10:20 AM ET run started at 9:17 PM ET), so the site
showed the previous day's close all day while live sites showed today's prices.

## How it works now

1. **Gate job.** Every run of the deploy workflow first runs `pipeline/freshness.py`. It reads the live
   `data/meta.json` and rebuilds only if that snapshot is older than the most recent refresh slot.
   Extra or late triggers finish in about 20 seconds without touching Yahoo Finance.
2. **Hourly GitHub cron** (`7 * * * *`) as a backup trigger.
3. **Outside timer (Supabase `pg_cron`)** that asks GitHub to run the workflow at the slot times. This is
   the part that makes the schedule dependable.

A push to `main`, or a manual run with **force** ticked, always rebuilds.

## One-time setup of the outside timer (about 5 minutes)

### Step 1: create a GitHub token (owner does this)

GitHub > Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token

- Name: `finstr-scheduler`
- Expiration: the longest offered (set a reminder to renew it)
- Repository access: **Only select repositories** > `FinSTR-app`
- Permissions > Repository permissions > **Actions: Read and write** (nothing else)

Copy the token. It is shown only once.

### Step 2: store the token in Supabase Vault (owner does this)

Supabase dashboard > project `qfepedbeyeefhmxqfpis` > **SQL Editor** > new query. Paste the token between
the quotes and run:

```sql
select vault.create_secret('PASTE_TOKEN_HERE', 'finstr_github_token', 'GitHub token: trigger FinSTR deploy');
```

### Step 3: create the timer (no secrets in it)

Run in the same SQL Editor:

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.finstr_trigger_refresh()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare tok text;
begin
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'finstr_github_token';
  perform net.http_post(
    url     := 'https://api.github.com/repos/Punch-k/FinSTR-app/actions/workflows/deploy.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || tok,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'finstr-scheduler'),
    body    := '{"ref":"main"}'::jsonb);
end $$;

revoke execute on function public.finstr_trigger_refresh() from public, anon, authenticated;

-- pg_cron uses UTC. Each slot is listed for both winter (EST) and summer (EDT) time;
-- the gate job skips whichever one is early, so no edits are needed when the clocks change.
select cron.schedule('finstr-refresh-1030', '35 14,15 * * 1-5', 'select public.finstr_trigger_refresh()');
select cron.schedule('finstr-refresh-1300', '5 17,18 * * 1-5',  'select public.finstr_trigger_refresh()');
select cron.schedule('finstr-refresh-1620', '25 20,21 * * 1-5', 'select public.finstr_trigger_refresh()');
```

### Step 4: test it

```sql
select public.finstr_trigger_refresh();
-- a second later:
select status_code, content from net._http_response order by created desc limit 1;
```

`status_code` **204** means GitHub accepted it, and a new "Build and deploy FinSTR" run appears under the repo's
Actions tab. A 401 or 403 means the token is wrong, expired, or missing the Actions permission.

## Checking it later

```sql
select jobname, schedule, active from cron.job where jobname like 'finstr%';
select start_time, status, return_message from cron.job_run_details order by start_time desc limit 10;
```

To stop it: `select cron.unschedule('finstr-refresh-1030');` (and the other two).

## Notes

- Free tier: Supabase can pause a free project after a week of low activity, which stops the timer. If the
  site's banner says "Prices are from ... not today" again, check the project is not paused. The hourly
  GitHub cron still runs as a backup.
- GitHub turns off `schedule:` crons in a public repo after 60 days with no commits. The Supabase timer
  uses `workflow_dispatch`, which is not affected.
