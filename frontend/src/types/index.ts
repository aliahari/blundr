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
  is_owner: boolean;
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

export type RatePhase = 'all' | 'opening' | 'middlegame' | 'endgame';

/** One week of the blunder rate (blunders per 100 of the user's moves). */
export interface BlunderRatePoint {
  week: string; // Monday the week starts on, YYYY-MM-DD
  games: number; // games with move data that week
  user_moves: number;
  blunders: number;
  rate: number | null; // null when there were no moves to count
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

export interface RefutationLinePly {
  move_uci: string;
  move_san: string;
  win_prob: number;
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
  win_prob_before: number;
  win_prob_after: number;
  win_prob_drop: number;
  refutation_line: RefutationLinePly[]; // follows move_played
  best_line: RefutationLinePly[]; // follows best_move; empty on pre-existing blunders
}

export interface BestReply {
  move_san: string;
  reply_uci: string | null;
  reply_san: string | null;
  eval_after_cp: number;
  game_over: boolean;
}

// --- Reviews ---

/** A review card as the API sends it. `kind` says what training item it
 * drills, and only that kind's payload is set. */
export interface ReviewCardInfo {
  card_id: number;
  kind: string;
  card_type: string;
  step: number; // which move of the item's line; 0 for every kind so far
  due_at: string;
  repetitions: number;
  lapses: number;
  blunder?: BlunderInfo | null;
  opening?: OpeningCardPayload | null;
  deviation?: DeviationCardPayload | null;
}

/** A blunder card: one move, trained from the blunderer's side ("avoid")
 * or the opponent's ("punish"). */
export interface BlunderReviewCard extends ReviewCardInfo {
  kind: 'blunder';
  card_type: 'avoid' | 'punish';
  blunder: BlunderInfo;
}

// --- Openings (Phase 2) ---

export type Color = 'white' | 'black';

export interface OpeningLabel {
  eco: string;
  name: string;
}

export interface ExplanationInfo {
  id: number;
  position_key: string;
  move_uci: string;
  source: 'owner' | 'claude';
  plan: string;
  why_this_move: string;
  pawn_structure: string | null;
  model: string | null;
}

export interface OpeningCardPayload {
  color: Color;
  line: string[]; // SAN from the start position to the card's position
  fen: string;
  answer_uci: string;
  answer_san: string;
  opening: OpeningLabel | null;
  theory_line: string | null;
  explanation: ExplanationInfo | null;
}

export interface DeviationCardPayload {
  color: Color;
  line: string[]; // SAN from the start to the opponent's mistake, inclusive
  fen: string;
  opponent_move_san: string;
  answer_uci: string;
  answer_san: string;
  opening: OpeningLabel | null;
  lichess_game_id: string;
  played_at: string;
}

/** An opening or punish card: both are "find the move after this line". */
export interface LineReviewCard extends ReviewCardInfo {
  kind: 'opening' | 'deviation';
}

export const isLineCard = (card: ReviewCardInfo): card is LineReviewCard =>
  (card.kind === 'opening' && card.opening != null) ||
  (card.kind === 'deviation' && card.deviation != null);

export interface TheoryAlternative {
  uci: string;
  san: string;
  line: string;
}

export interface RepertoireSuggestion {
  position_key: string;
  ply: number;
  move_uci: string;
  move_san: string;
  games: number;
  score: number;
  parent_key: string | null;
  opening: OpeningLabel | null;
  theory_line: string | null;
  theory_alternatives: TheoryAlternative[];
  avg_drop: number | null;
  better_uci: string | null;
  better_san: string | null;
  accepted_move_uci: string | null;
}

export interface RepertoireEntry {
  position_key: string;
  move_uci: string;
  move_san: string;
  parent_key: string | null;
  source: 'games' | 'manual';
  opening: OpeningLabel | null;
  card_id: number | null;
}

export interface RepertoireInfo {
  color: Color;
  games_used: number;
  suggestions: RepertoireSuggestion[];
  accepted: RepertoireEntry[];
}

export interface RepertoireChange {
  accepted: RepertoireEntry[];
  removed: number;
}

export interface MoveOption {
  uci: string;
  san: string;
  theory_line: string | null;
  games: number;
}

export interface DeviationInfo {
  game_id: number;
  lichess_game_id: string;
  played_at: string;
  ply: number;
  side: 'user' | 'opponent';
  position_key: string;
  expected_uci: string | null;
  played_uci: string;
  punish_uci: string | null;
  played_was_theory: boolean;
}

export const isBlunderCard = (card: ReviewCardInfo): card is BlunderReviewCard =>
  card.kind === 'blunder' && card.blunder != null;

export type ReviewGrade = 'again' | 'good' | 'easy';

export interface ReviewStats {
  due_now: number;
  new_remaining_today: number;
  total_cards: number;
  total_blunders: number;
}
