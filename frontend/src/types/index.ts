// Game types
export type GameType = 'all' | 'bullet' | 'blitz' | 'rapid' | 'classical' | 'correspondence' | 'standard';

// Period types
export type Period = '1month' | '3months' | '1year' | 'alltime';

// Player result
export type PlayerResult = 'win' | 'loss' | 'draw' | null;

// API Response Types
export interface PlayerInfo {
  id: string; // Canonical Lichess user ID (lowercase, stable identifier)
  name: string;
  rating: number | null;
  color: 'white' | 'black';
  result: PlayerResult;
}

export interface GameResponse {
  id: string;
  game_date: string; // Date in YYYY-MM-DD format
  created_at: number; // Unix timestamp in milliseconds
  white: PlayerInfo;
  black: PlayerInfo;
  result: string; // '1-0', '0-1', '1/2-1/2', '*'
  pgn: string;
  time_control: string;
  end_status: string;
  moves: string[];
  initial_fen: string;
}

export interface GameListResponse {
  games: GameResponse[];
  total: number;
  username: string;
  date_range: {
    start: string;
    end: string;
  };
}

export interface GameRequest {
  username: string;
  start_date: string; // YYYY-MM-DD
  end_date: string; // YYYY-MM-DD
  max_games?: number;
  game_type?: GameType;
}

// Form state
export interface FormState {
  username: string;
  period: Period;
  gameType: GameType;
}

// UI State
export type AppState = 'idle' | 'loading' | 'success' | 'error';

// --- Auth ---

export interface UserInfo {
  id: number;
  username: string;
  email: string | null;
  lichess_username: string | null;
  display_name: string | null;
  avatar_url: string | null;
}

// --- Settings ---

export interface SettingsInfo {
  username: string;
  email: string | null;
  display_name: string | null;
  avatar_url: string | null;
  lichess_username: string | null;
  sync_game_types: string[];
  sync_days_back: number;
  max_new_per_day: number;
}

export type SettingsUpdate = Partial<Omit<SettingsInfo, 'username'>>;

// --- Stats ---

export interface StatsOverview {
  games_analyzed: number;
  total_blunders: number;
  blunders_mastered: number;
  due_now: number;
  reviews_done: number;
}

export interface TimelinePoint {
  date: string;
  games: number;
  blunders: number;
  reviews: number;
  mastered_pct: number | null;
}

// --- Analysis ---

export interface AnalysisStatus {
  state: 'idle' | 'running' | 'done' | 'error' | 'skipped';
  games_total: number;
  games_done: number;
  blunders_found: number;
  error: string | null;
  last_synced_at?: string | null;
}

export interface BlunderInfo {
  id: number;
  game_lichess_id: string;
  played_at: string;
  user_color: 'white' | 'black';
  opponent: string;
  ply: number;
  fen_before: string;
  move_played_san: string;
  move_played_uci: string;
  best_move_san: string;
  best_move_uci: string;
  refutation_san: string | null;
  refutation_uci: string | null;
  eval_before_cp: number;
  eval_after_cp: number;
  win_prob_drop: number;
}

export interface BestReply {
  move_san: string;
  reply_uci: string | null;
  reply_san: string | null;
  eval_after_cp: number;
  game_over: boolean;
}

// --- Reviews ---

export interface ReviewCardInfo {
  card_id: number;
  due_at: string;
  repetitions: number;
  lapses: number;
  blunder: BlunderInfo;
}

export type ReviewGrade = 'again' | 'good' | 'easy';

export interface ReviewStats {
  due_now: number;
  new_remaining_today: number;
  total_cards: number;
  total_blunders: number;
}
