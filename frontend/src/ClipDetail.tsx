import { useEffect, useRef, useState } from 'react'

import {
  type Clip,
  formatBytes,
  formatClock,
  formatDate,
  formatDuration,
  keyframeGridMs,
  sourceBytes,
  sourceUrl,
  thumbnailUrl,
} from './api'

interface Props {
  clip: Clip
  hasPrev: boolean
  hasNext: boolean
  onClose: () => void
  onPrev: () => void
  onNext: () => void
  onRename: (title: string) => void
  onTogglePin: () => void
  onCopy: () => void
  onDelete: () => void
  onTrim: (startMs: number, endMs: number) => void
}

export function ClipDetail({
  clip,
  hasPrev,
  hasNext,
  onClose,
  onPrev,
  onNext,
  onRename,
  onTogglePin,
  onCopy,
  onDelete,
  onTrim,
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(clip.title ?? '')
  const [confirming, setConfirming] = useState(false)
  const [trimming, setTrimming] = useState(false)
  const [range, setRange] = useState<[number, number]>([0, clip.durationMs])
  const inputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  const grid = keyframeGridMs(clip)
  // The cut seeks to the nearest earlier keyframe, so show where it will
  // actually land rather than where the handle was dropped.
  const effectiveStart = Math.floor(range[0] / grid) * grid
  const selected = Math.max(0, range[1] - effectiveStart)

  // Reset per-clip UI state when arrow keys move to a different clip.
  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      // While renaming, arrows belong to the text field.
      if (editing) {
        if (event.key === 'Escape') setEditing(false)
        return
      }
      if (event.key === 'Escape') {
        // Back out of trimming first, so Escape does not close the whole sheet
        // and silently discard a selection.
        if (trimming) setTrimming(false)
        else onClose()
        return
      }
      // keydown still bubbles to window when the player has focus, so without
      // this an arrow key would seek the video and jump to the next clip at
      // the same time. Focused player wins: arrows scrub.
      const tag = (event.target as HTMLElement | null)?.tagName
      if (tag === 'VIDEO' || tag === 'INPUT' || tag === 'TEXTAREA') return

      if (event.key === 'ArrowLeft' && hasPrev) onPrev()
      else if (event.key === 'ArrowRight' && hasNext) onNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing, trimming, hasPrev, hasNext, onClose, onPrev, onNext])

  function startTrimming() {
    // Seed the range here rather than in an effect: the duration changes after
    // a trim, and reading it at the moment the panel opens is always current.
    setRange([0, clip.durationMs])
    setTrimming(true)
  }

  function startRenaming() {
    setDraft(clip.title ?? '')
    setEditing(true)
  }

  function markPoint(edge: 'start' | 'end') {
    const element = videoRef.current
    if (!element) return
    const at = element.currentTime * 1000
    setRange(([start, end]) =>
      edge === 'start'
        ? [Math.min(at, end - grid), end]
        : [start, Math.max(at, start + grid)],
    )
  }

  function previewRange() {
    const element = videoRef.current
    if (!element) return
    element.currentTime = effectiveStart / 1000
    void element.play()
  }

  function commitRename() {
    setEditing(false)
    const next = draft.trim()
    if (next !== (clip.title ?? '')) onRename(next)
  }

  const video = sourceUrl(clip)
  const meta = clip.captureMeta ?? {}
  const dimensions =
    meta.width && meta.height ? `${String(meta.width)}x${String(meta.height)}` : null

  return (
    <div className="overlay" onMouseDown={onClose}>
      <div className="sheet" onMouseDown={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          {editing ? (
            <input
              ref={inputRef}
              className="rename"
              value={draft}
              placeholder="Name this clip"
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename()
              }}
            />
          ) : (
            <h2 onClick={startRenaming} title="Click to rename">
              {clip.title || 'Untitled clip'}
            </h2>
          )}
          <button className="icon" onClick={onTogglePin} title={clip.favorite ? 'Unpin' : 'Pin'}>
            {clip.favorite ? '★' : '☆'}
          </button>
          <button className="icon" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>

        <div className="stage">
          {video ? (
            <video
              // Keyed on the URL, not the id: the URL carries a version that
              // changes when the clip is edited, and remounting is what makes
              // the player pick up the new file instead of the one it already
              // has loaded.
              key={video}
              ref={videoRef}
              src={video}
              poster={thumbnailUrl(clip) ?? undefined}
              controls
              autoPlay
              playsInline
              onTimeUpdate={() => {
                // While trimming, stop at the out point so playback previews
                // the cut instead of running past it.
                const element = videoRef.current
                if (!trimming || !element) return
                if (element.currentTime * 1000 >= range[1]) element.pause()
              }}
            />
          ) : (
            <div className="pending">No playable rendition ({clip.status})</div>
          )}
          <button className="nav prev" onClick={onPrev} disabled={!hasPrev} title="Previous (←)">
            ‹
          </button>
          <button className="nav next" onClick={onNext} disabled={!hasNext} title="Next (→)">
            ›
          </button>
        </div>

        <div className="chips">
          <span className="chip">{formatDuration(clip.durationMs)}</span>
          {dimensions && <span className="chip">{dimensions}</span>}
          {Boolean(meta.fps) && <span className="chip">{String(meta.fps)} fps</span>}
          <span className="chip">{formatBytes(sourceBytes(clip))}</span>
          {Boolean(meta.encoder) && <span className="chip">{String(meta.encoder)}</span>}
          <span className="chip">{clip.viewCount} views</span>
          <span className="chip">{formatDate(clip.capturedAt)}</span>
        </div>

        {trimming && (
          <div className="trim">
            <div className="trim-bar">
              <div
                className="trim-sel"
                style={{
                  left: `${(effectiveStart / clip.durationMs) * 100}%`,
                  width: `${(selected / clip.durationMs) * 100}%`,
                }}
              />
            </div>
            <div className="trim-controls">
              <button className="ghost" onClick={() => markPoint('start')}>
                Set start
              </button>
              <span className="trim-readout">
                {formatClock(effectiveStart)} to {formatClock(range[1])}
                <b> {formatClock(selected)}</b>
              </span>
              <button className="ghost" onClick={() => markPoint('end')}>
                Set end
              </button>
              <button className="ghost" onClick={previewRange}>
                Preview
              </button>
              <button
                className="primary"
                disabled={selected < 500}
                onClick={() => {
                  setTrimming(false)
                  onTrim(effectiveStart, range[1])
                }}
              >
                Trim
              </button>
              <button className="ghost" onClick={() => setTrimming(false)}>
                Cancel
              </button>
            </div>
            <p className="muted">
              Scrub the player, then set the points. Cuts land on keyframes every{' '}
              {grid / 1000}s, so the start snaps back to the nearest one. This
              replaces the clip and cannot be undone.
            </p>
          </div>
        )}

        <div className="sheet-actions">
          <input className="link" value={clip.shareUrl} readOnly onFocus={(e) => e.target.select()} />
          <button className="primary" onClick={onCopy}>
            Copy link
          </button>
          <a className="ghost" href={clip.shareUrl} target="_blank" rel="noreferrer">
            Open
          </a>
          {video && !trimming && (
            <button className="ghost" onClick={startTrimming}>
              Trim
            </button>
          )}
          {confirming ? (
            <>
              <button className="danger" onClick={onDelete}>
                Delete for good
              </button>
              <button className="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="ghost danger-text" onClick={() => setConfirming(true)}>
              Delete
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
