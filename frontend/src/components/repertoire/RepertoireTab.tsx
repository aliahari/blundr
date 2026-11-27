import { useState } from 'react';
import type { Color } from '../../types';
import BuilderView from './BuilderView';
import ColourHome from './ColourHome';
import OverviewView from './OverviewView';

/**
 * The Repertoire tab (specs/features/02-openings/repertoire-builder.md): the
 * step-by-step builder by default, the graph as a separate overview. Picked
 * positions become opening cards in Learn, where their explanations are read.
 */
export default function RepertoireTab({ onOpenLearn }: { onOpenLearn: () => void }) {
  const [color, setColor] = useState<Color>('white');
  const [mode, setMode] = useState<'home' | 'build' | 'overview'>('home');
  const [builderLine, setBuilderLine] = useState<string[] | null>(null);

  const switchColor = (c: Color) => {
    setColor(c);
    setBuilderLine(null);
    setMode('home');
  };

  return (
    <div className="repertoire-tab">
      <div className="repertoire-head">
        <div className="learn-tabs" role="tablist">
          {(['white', 'black'] as Color[]).map((c) => (
            <button key={c} type="button" role="tab" aria-selected={color === c}
              className={`tab ${color === c ? 'tab-active' : ''}`} onClick={() => switchColor(c)}>
              With {c === 'white' ? 'White' : 'Black'}
            </button>
          ))}
        </div>
      </div>
      {mode !== 'home' && (
        <button type="button" className="link-button colour-back" onClick={() => setMode('home')}>
          ← {color === 'white' ? 'White' : 'Black'} openings
        </button>
      )}
      {mode === 'home' ? (
        <ColourHome color={color} onBuild={() => { setBuilderLine(null); setMode('build'); }}
          onOverview={() => setMode('overview')} onLearn={onOpenLearn} />
      ) : mode === 'build' ? (
        <BuilderView color={color} initialLine={builderLine} onOpenOverview={() => setMode('overview')} />
      ) : (
        <OverviewView color={color}
          onBack={() => { setBuilderLine(null); setMode('build'); }}
          onOpenGap={(line) => { setBuilderLine(line); setMode('build'); }} />
      )}
    </div>
  );
}
