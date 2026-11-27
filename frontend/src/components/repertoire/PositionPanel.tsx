import { Chessboard } from 'react-chessboard';
import type { Color, PositionPanel as Panel } from '../../types';
import { evalText, GamesCell, pct } from './cells';
import { numberedSan } from './layoutGraph';

const BOARD_LIGHT = '#e9dcc3';
const BOARD_DARK = '#8b6b4a';
const ACT_MIN_GAMES = 2; // same as the server: a reply seen once is noise

interface Props {
  color: Color;
  panel: Panel;
  line: string[];
  busy: boolean;
  onPick: (uci: string) => void;
  onRemove: () => void;
  onSelectLine: (line: string[]) => void;
}

/** "the start" or the move that reached the position after `line`, e.g. "2…Nc6". */
function positionLabel(line: string[], sans: string[]): string {
  if (line.length === 0) return 'the first move';
  return numberedSan(line.length, sans[line.length - 1] ?? line[line.length - 1]);
}

export default function PositionPanel({
  color, panel, line, busy, onPick, onRemove, onSelectLine,
}: Props) {
  const side = color === 'white' ? 'White' : 'Black';
  const blocked = panel.pick_blocked_at;
  const rep = panel.repertoire_move;
  const arrows: Array<[any, any, string]> = rep ? [[rep.uci.slice(0, 2), rep.uci.slice(2, 4), '#5cb87a']] : [];

  return (
    <div className="position-panel">
      <nav className="position-crumbs" aria-label="Line">
        <button type="button" className="link-button" onClick={() => onSelectLine([])}>{side}</button>
        {panel.line_san.map((san, i) => (
          <span key={i}>
            {' ▸ '}
            <button type="button" className="link-button" onClick={() => onSelectLine(line.slice(0, i + 1))}
              aria-current={i === line.length - 1 ? 'step' : undefined}>
              {numberedSan(i + 1, san)}
            </button>
          </span>
        ))}
        <span className="position-crumbs-meta">
          {panel.opening ? ` · ${panel.opening.name}` : ''} · {panel.games} game{panel.games === 1 ? '' : 's'}
        </span>
      </nav>

      <div className="position-board">
        <Chessboard
          position={panel.fen}
          boardOrientation={color}
          arePiecesDraggable={false}
          customBoardStyle={{ borderRadius: '6px' }}
          customLightSquareStyle={{ backgroundColor: BOARD_LIGHT }}
          customDarkSquareStyle={{ backgroundColor: BOARD_DARK }}
          customArrows={arrows}
        />
      </div>

      {panel.user_to_move ? (
        <>
          {rep ? (
            <div className="position-rep">
              <h4>Your move here: {rep.san}</h4>
              <div className="row-actions">
                <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={onRemove}>
                  Remove
                </button>
                <span className="intro-note">Pick another move below to change it.</span>
              </div>
            </div>
          ) : (
            <h4>{blocked ? 'Moves here' : 'Choose your move here'}</h4>
          )}

          {blocked && (
            <p className="position-blocked">
              Your repertoire doesn't reach this position yet.{' '}
              <button type="button" className="link-button" onClick={() => onSelectLine(blocked)}>
                Start at {positionLabel(blocked, panel.line_san)}
              </button>
            </p>
          )}
          {panel.usual_move_note && <p className="position-note">{panel.usual_move_note}</p>}
          {panel.engine_busy && (
            <p className="intro-note">The engine is busy analysing games, so there are no evaluations right now.</p>
          )}

          <div className="table-scroll">
            <table className="position-table">
              <thead>
                <tr>
                  <th scope="col">Move</th>
                  <th scope="col">Engine</th>
                  <th scope="col">Masters</th>
                  <th scope="col">You</th>
                  <th scope="col"><span className="sr-only">Action</span></th>
                </tr>
              </thead>
              <tbody>
                {panel.candidates.map((c) => (
                  <tr key={c.uci} className={rep?.uci === c.uci ? 'is-repertoire' : undefined}>
                    <th scope="row">
                      <span className="cell-main">{c.san}</span>
                      {c.theory && <span className="cell-sub"><span className="theory-tag">theory</span></span>}
                    </th>
                    <td>
                      {c.engine_rank ? (
                        <><span className="cell-main">#{c.engine_rank}</span><span className="cell-sub">{evalText(c.eval_cp)}</span></>
                      ) : '—'}
                    </td>
                    <td><GamesCell games={c.master_games} share={c.master_share} score={c.master_score} /></td>
                    <td><GamesCell games={c.games} share={c.share} score={c.score} /></td>
                    <td>
                      {rep?.uci === c.uci ? (
                        <span className="muted">yours</span>
                      ) : !blocked && (
                        <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                          onClick={() => onPick(c.uci)} aria-label={`Pick ${c.san}`}>
                          Pick
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {panel.candidates.length > 0 && (
            <p className="table-legend">
              The percentage after the games is how often that move is chosen here. The score counts a
              win as 1 and a draw as ½: for the side to move in master games, for you in yours.
            </p>
          )}
          {panel.candidates.length === 0 && <p className="intro-note">No moves to suggest here.</p>}
        </>
      ) : (
        <>
          <h4>
            Your opponent's replies
            {panel.answered_pct !== null && (
              <span className="position-coverage"> · {Math.round(panel.answered_pct)}% of your games here have an answer</span>
            )}
          </h4>
          {blocked && (
            <p className="position-blocked">
              Your repertoire doesn't reach this position yet.{' '}
              <button type="button" className="link-button" onClick={() => onSelectLine(blocked)}>
                Start at {positionLabel(blocked, panel.line_san)}
              </button>
            </p>
          )}
          <div className="table-scroll">
            <table className="position-table">
              <thead>
                <tr>
                  <th scope="col">Reply</th>
                  <th scope="col">You</th>
                  <th scope="col">Masters</th>
                  <th scope="col"><span className="sr-only">Theory</span></th>
                  <th scope="col">Your answer</th>
                </tr>
              </thead>
              <tbody>
                {panel.replies.map((r) => {
                  const go = () => onSelectLine([...line, r.uci]);
                  const answer = r.answer_san
                    ? r.answer_san
                    : r.in_games && r.games < ACT_MIN_GAMES ? '—' : '+ add answer';
                  return (
                    <tr key={r.uci} className={`clickable${r.in_games ? '' : ' not-met'}`} onClick={go}>
                      <th scope="row">
                        <button type="button" className="link-button" onClick={(e) => { e.stopPropagation(); go(); }}>
                          {r.san}
                        </button>
                      </th>
                      <td>{r.in_games ? <>{r.games} <span className="muted">{pct(r.share)}</span></> : <span className="muted">not in your games</span>}</td>
                      <td>{pct(r.master_share)}</td>
                      <td>{r.theory && <span className="theory-tag">theory</span>}</td>
                      <td className={r.answer_san ? 'answer-set' : 'answer-missing'}>{answer}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {panel.replies.length === 0 && <p className="intro-note">No replies here in your games or in master games.</p>}
        </>
      )}
    </div>
  );
}
