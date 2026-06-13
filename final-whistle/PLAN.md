# Final Whistle — Build Plan & Architecture

A daily football **prediction + penalty mini-game**. Inspired by the 2026 global
tournament, but uses **zero protected marks** — fictional teams, original crests,
no official trophy/mascot/logos. Coins are **cosmetic-only and non-cashable** to
stay clear of gambling regulation.

## Stack
- **Frontend:** Next.js 14 (App Router, TypeScript) + Tailwind CSS
- **Backend/DB:** Supabase (Postgres + Auth + Row Level Security + Storage)
- **Auth:** Supabase Google OAuth
- **Mini-game:** React `<canvas>` (lighter than Phaser for a 30s tap game)
- **Deploy:** Vercel (frontend) + Supabase (managed DB)

## Scoring logic
Per prediction, settled by admin:
| Outcome | XP |
|---|---|
| Correct winner / draw | +10 |
| + correct goal difference (not exact) | +5 |
| Exact score | +25 (replaces the +5) |
| Each correct bonus event | +5 |
| Confidence multiplier on result XP | low ×1.0, med ×1.25, high ×1.5 |
| Wrong result at high confidence | −5 |

Streak: a "correct result" extends streak. Milestones (3/5/10/20) pay coins.
Coins also from: penalty mini-game, daily login, badge unlocks.

## Database schema (Supabase / Postgres)
- `profiles` — 1:1 with `auth.users`; xp, coins, streak, best_streak, fan_title,
  avatar_frame, is_admin.
- `teams` — fictional name, short_name, crest_url, color.
- `matches` — home/away team, kickoff_at, status, scores, bonus_event_defs (jsonb),
  bonus_event_results (jsonb), stage.
- `predictions` — user, match, predicted_winner, scores, confidence,
  bonus_events (jsonb), xp_awarded, is_settled. Unique(user, match).
- `badges` / `user_badges`.
- `leagues` / `league_members` (private join via code).
- `shootout_scores` — mini-game results.
- `coin_transactions` — ledger.

RLS: profiles & matches & leaderboards readable by all authed users; writes to
matches/settlement only by `is_admin`. Predictions: owner insert/select; update
blocked once match locks.

## API routes (Next.js Route Handlers)
- `POST /api/predictions` — create (rejects if kickoff passed)
- `GET  /api/matches` — today's fixtures
- `POST /api/admin/matches` / `PATCH /api/admin/matches` — admin CRUD
- `POST /api/admin/settle` — compute + award XP/streaks for a match
- `GET  /api/leaderboard?scope=daily|overall|league`
- `POST /api/shootout` — submit score, award coins
- `POST /api/leagues` / `POST /api/leagues/join`
- `GET  /api/share/[predictionId]` — OG share card image

## Page structure (App Router)
- `/` landing + Google login
- `/dashboard` today's matches, countdown, XP/coins/streak/rank
- `/matches/[id]` prediction form
- `/play` penalty shootout
- `/leaderboard` daily / overall / league with podium
- `/leagues` create & join private leagues
- `/rewards` badges, frames, fan titles
- `/profile` stats & cosmetics
- `/admin` matches, scores, settle, users
- `/share/[id]` public shareable card

## Component structure
GlassCard, NeonButton, StatPill, CountdownTimer, MatchCard, PredictionForm,
ConfidenceSelector, BonusEventPicker, LeaderboardPodium, LeaderboardTable,
BadgeGrid, AvatarFrame, ShareCard, PenaltyGame (canvas), NavBar, BottomNav.

## Monetization
1. **Cosmetic coin store** — avatar frames, fan titles, match-card themes.
2. **Season Pass** subscription (Stripe) — double XP, larger private leagues,
   exclusive frames.
3. **Rewarded ads** (mobile) for coins.
4. **Sponsor slots** on match cards — sell to local businesses.
5. **White-label resale** — the whole app as a template.

## Implementation sequence (sellable by step 6)
1. ✅ Scaffold + design system + Supabase schema
2. Auth (Google) + profile bootstrap
3. Matches + dashboard (read)
4. Predictions (write + kickoff lock)
5. Admin + settlement + scoring engine
6. Leaderboards  ← **minimum sellable product**
7. Penalty mini-game
8. Rewards / badges
9. Private leagues
10. Share cards
11. Polish, monetization hooks, deploy
