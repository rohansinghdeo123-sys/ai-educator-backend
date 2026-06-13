// Shared domain types for Final Whistle

export type MatchStatus = 'scheduled' | 'live' | 'finished' | 'settled';
export type PredictedResult = 'home' | 'away' | 'draw';
export type ConfidenceLevel = 'low' | 'medium' | 'high';

export interface Profile {
  id: string;
  username: string | null;
  avatar_url: string | null;
  xp: number;
  coins: number;
  streak: number;
  best_streak: number;
  fan_title: string;
  avatar_frame: string;
  is_admin: boolean;
  created_at: string;
}

export interface Team {
  id: string;
  name: string;
  short_name: string;
  crest_url: string | null;
  color: string;
}

export interface BonusEventDef {
  key: string;
  label: string;
}

export interface Match {
  id: string;
  home_team_id: string;
  away_team_id: string;
  kickoff_at: string;
  stage: string;
  status: MatchStatus;
  home_score: number | null;
  away_score: number | null;
  bonus_event_defs: BonusEventDef[];
  bonus_event_results: Record<string, boolean>;
  created_at: string;
  // joined
  home_team?: Team;
  away_team?: Team;
}

export interface Prediction {
  id: string;
  user_id: string;
  match_id: string;
  predicted_winner: PredictedResult;
  home_score: number;
  away_score: number;
  confidence: ConfidenceLevel;
  bonus_events: Record<string, boolean>;
  xp_awarded: number;
  is_settled: boolean;
  created_at: string;
}

export interface LeaderboardRow {
  id: string;
  username: string | null;
  avatar_url: string | null;
  xp: number;
  streak: number;
  fan_title: string;
  avatar_frame: string;
  rank: number;
}
