# ⚽ Final Whistle

A daily football **prediction + penalty shootout** game. Next.js + Supabase +
Tailwind. Premium football-night UI, mobile-first. Uses **no protected marks** —
fictional teams, original crests, coins are cosmetic-only (non-cashable).

See [`PLAN.md`](./PLAN.md) for the full architecture, scoring rules and roadmap.

## Features (V1 complete)
- 🔐 Google login (Supabase Auth)
- 🏟️ Dashboard: XP / coins / streak / rank, next-match countdown, fixtures
- 🎯 Predictions: winner, exact score, confidence multiplier, bonus events (kickoff-locked)
- ⚙️ Admin console: create teams & matches, one-click settlement
- 🧮 Scoring engine: XP, streaks, coins, badges on settle
- 🏆 Leaderboard with top-3 podium + your-rank pin
- 🥅 30-second penalty shootout (HTML canvas)
- 👥 Private leagues (create + join by code)
- 🎖️ Badges + cosmetic store (fan titles, avatar frames)
- 📤 Shareable prediction cards (WhatsApp/Instagram)

## Setup

### 1. Create a Supabase project
At [supabase.com](https://supabase.com), then:
- **SQL Editor** → paste & run [`supabase/schema.sql`](./supabase/schema.sql)
  (tables, RLS, profile bootstrap trigger, seed badges).
- **Authentication → Providers → Google**: enable it, add your Google OAuth
  client ID/secret ([Google Cloud Console](https://console.cloud.google.com)).
  Set the authorized redirect URI to:
  `https://YOUR-PROJECT.supabase.co/auth/v1/callback`
- **Authentication → URL Configuration**: add `http://localhost:3000` and your
  Vercel domain to redirect URLs.

### 2. Environment
```bash
cp .env.example .env.local   # fill in the three Supabase keys
```

### 3. Run
```bash
npm install
npm run dev          # http://localhost:3000
```

### 4. Make yourself admin
Sign in once, then in the Supabase SQL editor:
```sql
update profiles set is_admin = true where username ilike '%your-name%';
```
Open `/admin` to add teams, schedule matches, and settle results.

## Deploy (Vercel)
1. Push this folder to a Git repo and import it in Vercel (root = `final-whistle`).
2. Add the three env vars from `.env.example` in the Vercel project settings.
3. Add your Vercel domain to Supabase Auth redirect URLs.

## Monetization hooks (ready to wire)
- Cosmetic coin store (built) → sell coin packs via Stripe.
- Season Pass subscription (double XP, bigger leagues).
- Rewarded ads for coins on mobile.
- Sponsor slots on match cards.
- White-label resale — clone, rebrand, ship.

## Legal
Independent fan game. Not affiliated with FIFA or any official body. All team
names and crests are fictional. Coins have no cash value.
