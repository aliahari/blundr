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
  lichess_username: string | null;
  display_name: string | null;
  avatar_url: string | null;
  is_owner: boolean;
  lichess_connected: boolean; // false: the Lichess sign-in ended, sign in again
}

// --- Settings ---

export interface SettingsInfo {
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  lichess_username: string | null;
  sync_game_types: string[];
  sync_days_back: number;
  max_new_per_day: number;
}

export type SettingsUpdate = Partial<Omit<SettingsInfo, 'username' | 'lichess_username'>>;

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

export interface RepertoireDepthPoint {
  week: string; // Monday the week starts on, YYYY-MM-DD
  games: number; // games checked against the repertoire that week
  avg_moves: number | null; // average moves played inside the repertoire
  left_pct: number | null; // share of games that left it, in %
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

export interface RepertoireEntry {
  position_key: string;
  move_uci: string;
  move_san: string;
  parent_key: string | null;
  source: 'games' | 'manual';
  opening: OpeningLabel | null;
  card_id: number | null;
}

export interface RepertoireChange {
  accepted: RepertoireEntry[];
  removed: number;
  next: BuilderNext | null; // the builder's next step, after a pick
}

// --- The step-by-step builder (specs/features/02-openings/repertoire-builder.md) ---

export interface BuilderGap {
  line: string[];
  line_san: string[];
  fen: string;
  key: string;
  likelihood: number | null; // share of your games at your level that reach it
  last_reply_san: string | null;
  last_share: number | null;
  my_games: number;
  opening: OpeningLabel | null;
  candidates: CandidateRow[];
  engine_busy: boolean;
}

export interface BuilderNext {
  gap: BuilderGap | null; // null: everything at this goal is prepared
  prepared: number;
  left: number;
  goal: number;
  source: 'lichess' | 'masters';
  reconnect: boolean;
}

export const BUILDER_GOALS = [20, 50, 100, 200] as const;

// --- Repertoire graph (specs/features/02-openings/repertoire-graph.md) ---

export type GraphMark = 'you_left_theory';
export type TheorySource = 'masters' | 'dataset';

export interface GraphNode {
  key: string;
  user_to_move: boolean;
  games: number;
  ply: number;
  parent: string | null; // spanning-tree parent; the line is rebuilt from these
  opening: OpeningLabel | null;
  mark: GraphMark | null;
  folded: number;
}

export interface GraphEdge {
  parent: string;
  child: string;
  uci: string;
  san: string;
  games: number;
  w: number;
  d: number;
  l: number;
  master_games: number;
  theory: boolean;
  repertoire: boolean;
  transposition: boolean;
}

export interface RepertoireGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  todo: string[];
  theory_source: TheorySource;
  avg_moves_in_repertoire: number | null;
  empty_repertoire: boolean;
  gaps: string[]; // positions the builder would ask for at your goal
  thin: string[]; // positions rarely reached at your level
}

export interface CandidateRow {
  uci: string;
  san: string;
  engine_rank: number | null;
  eval_cp: number | null;
  master_games: number;
  master_share: number | null; // of the master games here
  master_score: number | null; // points per game for the side to move
  games: number;
  share: number | null; // of your moves here
  score: number | null; // points per game from your side
  theory: boolean;
  level_share: number | null; // of games at your level here (Lichess)
  level_score: number | null; // points per game for the side to move, at your level
  level_win?: number | null; // results at your level for the side to move
  level_draw?: number | null;
  level_loss?: number | null;
  opening?: OpeningLabel | null; // the opening the move leads to
}

export interface ReplyChoice {
  uci: string;
  san: string;
  opening: OpeningLabel | null;
  share: number;
  likelihood: number | null; // how often you reach the position after it
  completion: number | null; // how much below it you've prepared
  in_scope: boolean;
  answered: boolean;
}

/** One position in the builder's navigator. */
export interface ExplorePosition {
  line: string[];
  line_san: string[];
  fen: string;
  key: string;
  user_to_move: boolean;
  opening: OpeningLabel | null;
  likelihood: number | null;
  completion: number | null;
  goal: number;
  prepared: number;
  left: number;
  source: 'lichess' | 'masters';
  reconnect: boolean;
  repertoire_move: string | null;
  pick_blocked_at: string[] | null;
  replies: ReplyChoice[];
  candidates: CandidateRow[];
  engine_busy: boolean;
}

export interface ReplyRow {
  uci: string;
  san: string;
  games: number;
  share: number;
  score: number | null;
  master_share: number | null;
  theory: boolean;
  answer_san: string | null;
  in_games: boolean;
}

export interface PositionPanel {
  key: string;
  fen: string;
  line_san: string[];
  user_to_move: boolean;
  opening: OpeningLabel | null;
  games: number;
  repertoire_move: { uci: string; san: string } | null;
  candidates: CandidateRow[];
  replies: ReplyRow[];
  answered_pct: number | null;
  pick_blocked_at: string[] | null;
  theory_source: TheorySource;
  engine_busy: boolean;
  usual_move_note: string | null;
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
