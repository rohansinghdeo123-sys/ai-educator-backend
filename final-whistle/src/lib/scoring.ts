// =====================================================================
// Scoring engine — pure functions, unit-testable, shared by the settle API.
// =====================================================================
import type { ConfidenceLevel, PredictedResult } from './types';

export const SCORING = {
  WINNER_CORRECT: 10,
  GOAL_DIFF_CORRECT: 5, // right winner + right margin, but not exact score
  EXACT_SCORE: 25, // replaces GOAL_DIFF_CORRECT when scoreline is exact
  BONUS_EVENT_EACH: 5,
  HIGH_CONF_PENALTY: -5, // wrong result while confident
  CONFIDENCE_MULT: { low: 1.0, medium: 1.25, high: 1.5 } as Record<ConfidenceLevel, number>,
  STREAK_MILESTONES: { 3: 25, 5: 50, 10: 150, 20: 400 } as Record<number, number>,
} as const;

export interface ActualMatch {
  home_score: number;
  away_score: number;
  bonus_event_results: Record<string, boolean>;
}

export interface PredictionInput {
  predicted_winner: PredictedResult;
  home_score: number;
  away_score: number;
  confidence: ConfidenceLevel;
  bonus_events: Record<string, boolean>;
}

export interface ScoreBreakdown {
  xp: number;
  resultCorrect: boolean;
  exactScore: boolean;
  goalDiffCorrect: boolean;
  bonusHits: number;
  lines: { label: string; xp: number }[];
}

function winnerFromScore(home: number, away: number): PredictedResult {
  if (home > away) return 'home';
  if (away > home) return 'away';
  return 'draw';
}

/**
 * Compute XP for a single prediction against the actual result.
 * Result XP (winner + scoreline) is scaled by the confidence multiplier;
 * bonus-event XP is flat. A wrong result at high confidence costs points.
 */
export function computeScore(pred: PredictionInput, actual: ActualMatch): ScoreBreakdown {
  const lines: { label: string; xp: number }[] = [];
  const actualWinner = winnerFromScore(actual.home_score, actual.away_score);
  const resultCorrect = pred.predicted_winner === actualWinner;

  const exactScore =
    pred.home_score === actual.home_score && pred.away_score === actual.away_score;
  const predMargin = pred.home_score - pred.away_score;
  const actualMargin = actual.home_score - actual.away_score;
  const goalDiffCorrect = resultCorrect && predMargin === actualMargin && !exactScore;

  let resultXp = 0;
  if (resultCorrect) {
    resultXp += SCORING.WINNER_CORRECT;
    lines.push({ label: 'Correct result', xp: SCORING.WINNER_CORRECT });
    if (exactScore) {
      resultXp += SCORING.EXACT_SCORE;
      lines.push({ label: 'Exact scoreline', xp: SCORING.EXACT_SCORE });
    } else if (goalDiffCorrect) {
      resultXp += SCORING.GOAL_DIFF_CORRECT;
      lines.push({ label: 'Correct goal difference', xp: SCORING.GOAL_DIFF_CORRECT });
    }
  }

  // Apply confidence multiplier to the result portion only.
  const mult = SCORING.CONFIDENCE_MULT[pred.confidence];
  let xp = Math.round(resultXp * mult);
  if (resultXp > 0 && mult !== 1) {
    lines.push({ label: `Confidence ×${mult}`, xp: xp - resultXp });
  }

  // Penalty for being confidently wrong.
  if (!resultCorrect && pred.confidence === 'high') {
    xp += SCORING.HIGH_CONF_PENALTY;
    lines.push({ label: 'Confidently wrong', xp: SCORING.HIGH_CONF_PENALTY });
  }

  // Bonus events: +5 for each correctly predicted event outcome.
  let bonusHits = 0;
  for (const [key, predicted] of Object.entries(pred.bonus_events)) {
    if (key in actual.bonus_event_results && actual.bonus_event_results[key] === predicted) {
      bonusHits += 1;
    }
  }
  if (bonusHits > 0) {
    const bonusXp = bonusHits * SCORING.BONUS_EVENT_EACH;
    xp += bonusXp;
    lines.push({ label: `${bonusHits} bonus event(s)`, xp: bonusXp });
  }

  return { xp, resultCorrect, exactScore, goalDiffCorrect, bonusHits, lines };
}

/** Coins paid when a user reaches a streak milestone (0 if not a milestone). */
export function streakMilestoneReward(streak: number): number {
  return SCORING.STREAK_MILESTONES[streak] ?? 0;
}
