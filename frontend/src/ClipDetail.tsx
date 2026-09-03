import { useEffect, useRef, useState } from 'react'

import {
  type Clip,
  formatBytes,
  formatDate,
  formatDuration,
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
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(clip.title ?? '')
  const [confirming, setConfirming] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Reset per-clip UI state when arrow keys move to a different clip.
  useEffect(() => {
    setEditing(false)
    setConfirming(false)
    setDraft(clip.title ?? '')
  }, [clip.clipId, clip.title])

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
        onClose()
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
  }, [editing, hasPrev, hasNext, onClose, onPrev, onNext])

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
            <h2 onClick={() => setEditing(true)} title="Click to rename">
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
              key={clip.clipId}
              src={video}
              poster={thumbnailUrl(clip) ?? undefined}
              controls
              autoPlay
              playsInline
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

        <div className="sheet-actions">
          <input className="link" value={clip.shareUrl} readOnly onFocus={(e) => e.target.select()} />
          <button className="primary" onClick={onCopy}>
            Copy link
          </button>
          <a className="ghost" href={clip.shareUrl} target="_blank" rel="noreferrer">
            Open
          </a>
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
