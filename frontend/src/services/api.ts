import {
  GameRequest,
  GameListResponse,
  GameType,
  UserInfo,
  AnalysisStatus,
  BestReply,
  BlunderInfo,
  BlunderRatePoint,
  RepertoireDepthPoint,
  Color,
  DeviationInfo,
  PositionPanel,
  RepertoireGraph,
  ExplanationInfo,
  RatePhase,
  RepertoireChange,
  BuilderNext,
  ExplorePosition,
  LinePractice,
  RepertoireSummary,
  ReviewCardInfo,
  ReviewGrade,
  ReviewStats,
  SettingsInfo,
  SettingsUpdate,
  StatsOverview,
  TimelinePoint,
} from '../types';

const API_BASE_URL = '/api';
const TOKEN_KEY = 'blundr_token';

// --- Token management ---

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

/**
 * Structured API error. The backend's global exception handlers (see
 * app/main.py) return a machine-readable `error` discriminant alongside
 * `detail` (e.g. "user_not_found", "rate_limit_exceeded"), so callers can
 * branch on `code` for tailored messaging instead of pattern-matching a
 * freeform string.
 */
export class ApiError extends Error {
  code: string;
  status: number;
  retryAfter?: number;

  constructor(message: string, code: string, status: number, retryAfter?: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/**
 * Calculate date range based on period
 */
export const getDateRange = (period: string): { start: string; end: string } => {
  const today = new Date();
  const end = new Date(today);
  const start = new Date(today);

  switch (period) {
    case '1month':
      start.setMonth(start.getMonth() - 1);
      break;
    case '3months':
      start.setMonth(start.getMonth() - 3);
      break;
    case '1year':
      start.setFullYear(start.getFullYear() - 1);
      break;
    case 'alltime':
    default:
      // Set to a reasonable start date (Lichess was founded in 2010)
      start.setFullYear(2010, 0, 1);
      break;
  }

  return {
    start: formatDate(start),
    end: formatDate(end),
  };
};

/**
 * Format date as YYYY-MM-DD
 */
const formatDate = (date: Date): string => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

/**
 * Map frontend game type to backend game type
 */
const mapGameType = (gameType: string): GameType => {
  const mapping: Record<string, GameType> = {
    'bullet': 'bullet',
    'blitz': 'blitz',
    'rapid': 'rapid',
  };
  return mapping[gameType] || 'all';
};

/**
 * Fetch games from the backend API
 */
export const fetchGames = async (
  username: string,
  period: string,
  gameType: string,
  maxGames: number = 50
): Promise<GameListResponse> => {
  const { start, end } = getDateRange(period);
  const backendGameType = mapGameType(gameType);

  const request: GameRequest = {
    username,
    start_date: start,
    end_date: end,
    max_games: maxGames,
    game_type: backendGameType,
  };

  const response = await fetch(`${API_BASE_URL}/games/fetch`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.detail || 'Failed to fetch games',
      body.error || 'unknown_error',
      response.status,
      body.retry_after
    );
  }

  return response.json();
};

/**
 * Fetch wrapper that attaches the JWT and normalizes errors to ApiError.
 */
const authFetch = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      describeErrorDetail(body.detail),
      body.error || 'unknown_error',
      response.status,
      body.retry_after
    );
  }

  return response.json();
};

/**
 * FastAPI puts a string in `detail` for HTTPExceptions, but an ARRAY of
 * {loc, msg} objects for request-validation errors — flatten the latter
 * into something a human can act on ("password: String should have at
 * least 8 characters").
 */
const describeErrorDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((e: any) => {
        const field = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : null;
        return e?.msg ? (field ? `${field}: ${e.msg}` : e.msg) : null;
      })
      .filter(Boolean);
    if (parts.length > 0) return parts.join('; ');
  }
  return 'Request failed';
};

// --- Auth API ---

/** Where the "Sign in with Lichess" button points: the server runs the sign-in. */
export const LICHESS_SIGN_IN_URL = `${API_BASE_URL}/auth/lichess/start`;

export const getMe = async (): Promise<UserInfo> => authFetch<UserInfo>('/auth/me');

// --- Analysis API ---

export const startAnalysis = async (
  lichessUsername: string,
  period: string,
  gameType: string,
  maxGames: number = 20
): Promise<AnalysisStatus> => {
  const { start, end } = getDateRange(period);
  return authFetch<AnalysisStatus>('/analysis/start', {
    method: 'POST',
    body: JSON.stringify({
      lichess_username: lichessUsername,
      start_date: start,
      end_date: end,
      max_games: maxGames,
      game_type: mapGameType(gameType),
    }),
  });
};

export const getAnalysisStatus = async (): Promise<AnalysisStatus> =>
  authFetch<AnalysisStatus>('/analysis/status');

export const getBlunders = async (limit: number = 100): Promise<BlunderInfo[]> =>
  authFetch<BlunderInfo[]>(`/analysis/blunders?limit=${limit}`);

export const getBestReply = async (fen: string, moveUci: string): Promise<BestReply> =>
  authFetch<BestReply>('/analysis/best-reply', {
    method: 'POST',
    body: JSON.stringify({ fen, move_uci: moveUci }),
  });

/**
 * Sync games + analysis from the user's saved preferences.
 * Non-forced syncs are skipped server-side within 24h of the last one;
 * force=true (the Sync now button) always runs.
 */
export const startSync = async (force: boolean = false): Promise<AnalysisStatus> =>
  authFetch<AnalysisStatus>(`/analysis/sync${force ? '?force=true' : ''}`, { method: 'POST' });

// --- Settings API ---

export const getSettings = async (): Promise<SettingsInfo> =>
  authFetch<SettingsInfo>('/settings');

export const updateSettings = async (update: SettingsUpdate): Promise<SettingsInfo> =>
  authFetch<SettingsInfo>('/settings', {
    method: 'PUT',
    body: JSON.stringify(update),
  });

// --- Stats API ---

export const getStatsOverview = async (): Promise<StatsOverview> =>
  authFetch<StatsOverview>('/stats/overview');

export const getTimeline = async (days: number = 30): Promise<TimelinePoint[]> =>
  authFetch<TimelinePoint[]>(`/stats/timeline?days=${days}`);

export const getBlunderRate = async (
  weeks: number = 12,
  phase: RatePhase = 'all',
): Promise<BlunderRatePoint[]> =>
  authFetch<BlunderRatePoint[]>(`/stats/blunder-rate?weeks=${weeks}&phase=${phase}`);

export const getRepertoireDepth = async (weeks: number = 12): Promise<RepertoireDepthPoint[]> =>
  authFetch<RepertoireDepthPoint[]>(`/stats/repertoire-depth?weeks=${weeks}`);

// --- Reviews API ---

/**
 * Due cards. `kinds` keeps a queue to some kinds of card, each with its own
 * daily limit of new ones; `extraNew` lets that many more new items in past
 * it ("Review more").
 */
export const getDueCards = async (
  limit: number = 20, opts: { kinds?: string[]; extraNew?: number } = {},
): Promise<ReviewCardInfo[]> => {
  const params = new URLSearchParams({ limit: String(limit) });
  for (const k of opts.kinds ?? []) params.append('kind', k);
  if (opts.extraNew) params.set('extra_new', String(opts.extraNew));
  return authFetch<ReviewCardInfo[]>(`/reviews/due?${params}`);
};

/** How many more new items "Review more" lets in. */
export const REVIEW_MORE_STEP = 10;

/** The next whole line to practise, or null when no opening move is due. */
export const getLine = async (extraNew: number = 0): Promise<LinePractice | null> =>
  authFetch<LinePractice | null>(`/reviews/line${extraNew ? `?extra_new=${extraNew}` : ''}`);

export const answerCard = async (cardId: number, grade: ReviewGrade): Promise<ReviewCardInfo> =>
  authFetch<ReviewCardInfo>(`/reviews/${cardId}`, {
    method: 'POST',
    body: JSON.stringify({ grade }),
  });

export const getReviewStats = async (): Promise<ReviewStats> =>
  authFetch<ReviewStats>('/reviews/stats');

/**
 * Health check
 */
export const healthCheck = async (): Promise<boolean> => {
  try {
    const response = await fetch(`${API_BASE_URL}/health`);
    return response.ok;
  } catch {
    return false;
  }
};

// --- Repertoire (Phase 2) ---

export const setRepertoireMove = async (
  color: Color, positionKey: string, moveUci: string, withNext: boolean = true,
): Promise<RepertoireChange> =>
  authFetch<RepertoireChange>(`/repertoire/${color}/position${withNext ? '' : '?with_next=false'}`, {
    method: 'PUT',
    body: JSON.stringify({ position_key: positionKey, move_uci: moveUci }),
  });

export const removeRepertoirePosition = async (
  color: Color, positionKey: string,
): Promise<RepertoireChange> =>
  authFetch<RepertoireChange>(`/repertoire/${color}/position`, {
    method: 'DELETE',
    body: JSON.stringify({ position_key: positionKey }),
  });

export const getRepertoireGraph = async (
  color: Color, unfold: string[] = [],
): Promise<RepertoireGraph> => {
  const query = unfold.map((key) => `unfold=${encodeURIComponent(key)}`).join('&');
  return authFetch<RepertoireGraph>(`/repertoire/${color}/graph${query ? `?${query}` : ''}`);
};

/** Everything the panel shows for the position after `line` (uci moves from the start). */
export const getRepertoirePosition = async (color: Color, line: string[]): Promise<PositionPanel> =>
  authFetch<PositionPanel>(`/repertoire/${color}/position?line=${encodeURIComponent(line.join(','))}`);

/** A colour's page: % complete, saved positions, % mastered, cards due. */
export const getRepertoireSummary = async (color: Color): Promise<RepertoireSummary> =>
  authFetch<RepertoireSummary>(`/repertoire/${color}/summary`);

/** One position in the builder's navigator (`line`: uci moves from the start). */
export const getExplore = async (color: Color, line: string[]): Promise<ExplorePosition> =>
  authFetch<ExplorePosition>(`/repertoire/${color}/explore?line=${encodeURIComponent(line.join(','))}`);

/** The builder's next step: the likeliest position you haven't prepared (or the one `line` leads to). */
export const getNext = async (color: Color, line?: string[]): Promise<BuilderNext> =>
  authFetch<BuilderNext>(`/repertoire/${color}/next${line ? `?line=${encodeURIComponent(line.join(','))}` : ''}`);

/** Set the depth: prepare every common position up to your move at move `depth`. */
export const setDepth = async (color: Color, depth: number): Promise<BuilderNext> =>
  authFetch<BuilderNext>(`/repertoire/${color}/depth`, { method: 'PUT', body: JSON.stringify({ depth }) });

export const getDeviations = async (): Promise<DeviationInfo[]> =>
  authFetch<DeviationInfo[]>('/repertoire/deviations');

/** The explanation for a move, or null when there is none yet. */
export const getExplanation = async (
  positionKey: string, moveUci: string,
): Promise<ExplanationInfo | null> => {
  try {
    return await authFetch<ExplanationInfo>(
      `/openings/explanations?position_key=${encodeURIComponent(positionKey)}&move_uci=${moveUci}`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
};

export const writeExplanation = async (body: {
  position_key: string; move_uci: string; plan: string; why_this_move: string; pawn_structure: string;
}): Promise<ExplanationInfo> =>
  authFetch<ExplanationInfo>('/openings/explanations', { method: 'PUT', body: JSON.stringify(body) });

export const reportExplanation = async (id: number, note: string): Promise<{ report_count: number }> =>
  authFetch<{ report_count: number }>(`/openings/explanations/${id}/report`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  });
