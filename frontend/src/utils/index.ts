import { GameResponse } from '../types';

/**
 * Get user's result from a game.
 * Matches on the player's canonical Lichess id (always lowercase) rather
 * than display name, since name comparisons that only lowercase one side
 * are fragile against the many ways a username can differ from its own id.
 */
export const getUserResult = (game: GameResponse, username: string): string | null => {
  const userLower = username.toLowerCase();

  if (game.white.id.toLowerCase() === userLower) {
    return game.white.result;
  }
  if (game.black.id.toLowerCase() === userLower) {
    return game.black.result;
  }
  return null;
};

/**
 * Get user's color from a game
 */
export const getUserColor = (game: GameResponse, username: string): 'white' | 'black' | null => {
  const userLower = username.toLowerCase();

  if (game.white.id.toLowerCase() === userLower) {
    return 'white';
  }
  if (game.black.id.toLowerCase() === userLower) {
    return 'black';
  }
  return null;
};

/**
 * Get result class for styling
 */
export const getResultClass = (result: string | null): string => {
  if (!result) return '';
  return result === 'win' ? 'win' : result === 'loss' ? 'loss' : 'draw';
};

/**
 * Format date for display
 */
export const formatDisplayDate = (dateString: string): string => {
  const date = new Date(dateString);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  // Check if today
  if (date.toDateString() === today.toDateString()) {
    return 'Today';
  }
  
  // Check if yesterday
  if (date.toDateString() === yesterday.toDateString()) {
    return 'Yesterday';
  }

  // Check if this year
  if (date.getFullYear() === today.getFullYear()) {
    const options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
    return date.toLocaleDateString('en-US', options);
  }

  // Otherwise show full date
  const options: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'short', day: 'numeric' };
  return date.toLocaleDateString('en-US', options);
};

/**
 * Format rating for display
 */
export const formatRating = (rating: number | null): string => {
  if (rating === null) return '?';
  return rating.toLocaleString();
};

/**
 * Get time control display name
 */
export const formatTimeControl = (timeControl: string): string => {
  if (!timeControl) return 'Unknown';
  
  // Map common time controls to friendly names
  const mapping: Record<string, string> = {
    '1+0': '1|0 Bullet',
    '2+1': '2|1 Bullet',
    '3+0': '3|0 Blitz',
    '3+2': '3|2 Blitz',
    '5+0': '5|0 Blitz',
    '5+3': '5|3 Blitz',
    '10+0': '10|0 Rapid',
    '10+5': '10|5 Rapid',
    '15+10': '15|10 Rapid',
    '30+0': '30|0 Classical',
    '60+30': '60|30 Classical',
  };
  
  return mapping[timeControl] || timeControl;
};

/**
 * Debounce function for search inputs
 */
export const debounce = <F extends (...args: Parameters<F>) => ReturnType<F>>(
  func: F,
  wait: number
): (...args: Parameters<F>) => void => {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return (...args: Parameters<F>) => {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
    timeoutId = setTimeout(() => {
      func(...args);
    }, wait);
  };
};
