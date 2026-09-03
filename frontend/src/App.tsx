import { useCallback, useEffect, useState } from 'react'

import {
  type Clip,
  copyText,
  deleteClip,
  formatDate,
  formatDuration,
  patchClip,
  thumbnailUrl,
  trimClip,
} from './api'
import { type HeldClip, type Sources, getBridge, isDesktop } from './bridge'
import { ClipDetail } from './ClipDetail'
import { EditableTitle } from './EditableTitle'
import { useHeldClips } from './useHeldClips'
import { usePreferences } from './usePreferences'
import { type RecentLink, clearRecent, loadRecent, pushRecent } from './recentLinks'
import { type Filter, useClips } from './useClips'

export default function App() {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [recent, setRecent] = useState<RecentLink[]>(() => loadRecent())
  const [showRecent, setShowRecent] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const { items, loading, error, hasMore, loadMore, refresh, replace, remove } = useClips(
    search,
    filter,
  )
  const { held, heldBytes, busy, generate, discard, discardAll, rename } = useHeldClips()
  const preferences = usePreferences()

  // Settings live in the desktop shell, so the button only exists there. The
  // tray menu is the other way in, and Windows hides new tray icons behind the
  // overflow chevron, which makes it a poor only way in.
  const [desktop, setDesktop] = useState(false)
  useEffect(() => {
    void isDesktop().then(setDesktop)
  }, [])

  // Which display is being recorded. Worth having here and not only in
  // settings: it is the one setting you change because of what is on screen
  // right now, and walking to another page to do it loses the moment.
  const [sources, setSources] = useState<Sources | null>(null)
  const [source, setSource] = useState(0)
  useEffect(() => {
    if (!desktop) return
    void getBridge().then((bridge) =>
      bridge?.sources().then((found) => {
        setSources(found)
        setSource(found.current)
      }),
    )
  }, [desktop])

  const chooseSource = useCallback(
    async (index: number) => {
      const previous = source
      setSource(index)
      const bridge = await getBridge()
      const result = await bridge?.set_source(index)
      if (result && !result.ok) {
        window.alert(`Could not change the source: ${result.message}`)
        setSource(previous)
      }
    },
    [source],
  )

  // Held clips are hidden by a search or the pinned filter: neither can apply
  // to something the server has never seen.
  const showHeld = held.length > 0 && !search && filter === 'all'

  const shareHeld = useCallback(
    async (clip: HeldClip) => {
      setToast('Uploading...')
      const result = await generate(clip)
      if (!result.ok || !result.url) {
        setToast(result.message ?? 'Could not generate a link')
        return
      }
      const copied = preferences.copy_link_automatically
        ? await copyText(result.url)
        : false
      setRecent(
        pushRecent({
          slug: result.url.split('/').pop() ?? result.url,
          url: result.url,
          title: 'New clip',
        }),
      )
      setToast(copied ? 'Link copied' : `Link ready: ${result.url}`)
      refresh()
    },
    [generate, refresh, preferences.copy_link_automatically],
  )

  // Debounce so typing does not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 250)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 2200)
    return () => clearTimeout(timer)
  }, [toast])

  const selected = selectedIndex !== null ? (items[selectedIndex] ?? null) : null

  const copyLink = useCallback(async (clip: Clip) => {
    const copied = await copyText(clip.shareUrl)
    // Record it either way. If the browser blocked the clipboard the link is
    // still the thing you wanted, and this list is how you get back to it.
    setRecent(
      pushRecent({
        slug: clip.publicSlug,
        url: clip.shareUrl,
        title: clip.title || 'Untitled clip',
      }),
    )
    setToast(copied ? 'Link copied' : 'Clipboard blocked. Saved under Recent links.')
  }, [])

  const handleRename = useCallback(
    async (clip: Clip, title: string) => {
      try {
        replace(await patchClip(clip.clipId, { title: title || null }))
      } catch {
        setToast('Rename failed')
      }
    },
    [replace],
  )

  const handleTogglePin = useCallback(
    async (clip: Clip) => {
      try {
        const updated = await patchClip(clip.clipId, { favorite: !clip.favorite })
        // Unpinning while looking at the pinned view removes it from the list.
        if (filter === 'pinned' && !updated.favorite) {
          remove(updated.clipId)
          setSelectedIndex(null)
        } else {
          replace(updated)
        }
        setToast(updated.favorite ? 'Pinned' : 'Unpinned')
      } catch {
        setToast('Could not update that clip')
      }
    },
    [filter, replace, remove],
  )

  const handleTrim = useCallback(
    async (clip: Clip, startMs: number, endMs: number) => {
      setToast('Trimming...')
      try {
        const updated = await trimClip(clip.clipId, startMs, endMs)
        replace(updated)
        setToast(`Trimmed to ${(updated.durationMs / 1000).toFixed(1)}s`)
      } catch (err) {
        setToast(err instanceof Error ? `Trim failed: ${err.message}` : 'Trim failed')
      }
    },
    [replace],
  )

  const handleDelete = useCallback(
    async (clip: Clip) => {
      try {
        await deleteClip(clip.clipId)
        remove(clip.clipId)
        setSelectedIndex(null)
        setToast('Clip deleted')
      } catch {
        setToast('Delete failed')
      }
    },
    [remove],
  )

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <h1>Arete</h1>
        </div>
        <span className="count">
          {items.length}
          {hasMore ? '+' : ''}
        </span>

        <div className="tabs">
          <button
            className={filter === 'all' ? 'on' : ''}
            onClick={() => {
              setFilter('all')
              setSelectedIndex(null)
            }}
          >
            All
          </button>
          <button
            className={filter === 'pinned' ? 'on' : ''}
            onClick={() => {
              setFilter('pinned')
              setSelectedIndex(null)
            }}
          >
            Pinned
          </button>
        </div>

        <input
          className="search"
          value={searchInput}
          placeholder="Search titles and slugs"
          onChange={(e) => setSearchInput(e.target.value)}
        />

        <button className="ghost" onClick={() => setShowRecent((v) => !v)}>
          Recent links {recent.length > 0 && <span className="badge">{recent.length}</span>}
        </button>
        <button className="ghost" onClick={refresh} title="Reload">
          &#8635;
        </button>
        {sources && (
          <select
            className="source"
            title="Which display Arete is recording"
            value={source}
            onChange={(e) => void chooseSource(Number(e.target.value))}
          >
            {sources.displays.map((display) => (
              <option key={display.index} value={display.index}>
                {display.label}
              </option>
            ))}
          </select>
        )}
        {desktop && (
          <button
            className="ghost"
            onClick={() => void getBridge().then((bridge) => bridge?.open_settings())}
            title="Settings"
          >
            Settings
          </button>
        )}
      </header>

      {showRecent && (
        <section className="recent">
          <div className="recent-head">
            <strong>Links you copied</strong>
            <span className="muted">stored in this browser</span>
            {recent.length > 0 && (
              <button className="ghost" onClick={() => setRecent(clearRecent())}>
                Clear
              </button>
            )}
          </div>
          {recent.length === 0 ? (
            <p className="muted">Nothing yet. Copy a link and it shows up here.</p>
          ) : (
            <ul>
              {recent.map((link) => (
                <li key={link.slug}>
                  <button
                    className="relink"
                    title="Copy again"
                    onClick={() => {
                      void copyText(link.url).then((ok) =>
                        setToast(ok ? 'Link copied' : 'Clipboard blocked by the browser'),
                      )
                    }}
                  >
                    {link.title}
                  </button>
                  <a href={link.url} target="_blank" rel="noreferrer" className="muted mono">
                    /c/{link.slug}
                  </a>
                  <span className="muted">{formatDate(link.copiedAt)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {showHeld && (
        <div
          className={`held-bar${
            heldBytes > preferences.held_warning_gb * 1_073_741_824 ? ' heavy' : ''
          }`}
        >
          <strong>{held.length}</strong>
          <span>
            clip{held.length === 1 ? '' : 's'} on this PC, not shared yet
          </span>
          <span className="mono">{(heldBytes / 1_073_741_824).toFixed(2)} GB</span>
          <button className="ghost" disabled={busy !== null} onClick={() => void discardAll()}>
            {busy === 'all' ? 'Discarding...' : 'Discard all'}
          </button>
        </div>
      )}

      {error && (
        <div className="error">
          {error}
          <button className="ghost" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      {!loading && items.length === 0 && held.length === 0 && !error ? (
        <div className="empty">
          {search || filter === 'pinned' ? (
            <>
              <strong>Nothing matches that</strong>
              Try a different search, or clear the filter.
            </>
          ) : (
            <>
              <strong>No clips yet</strong>
              Press F9 in game and the moment lands here, ready for a link.
            </>
          )}
        </div>
      ) : (
        <div className="grid">
          {showHeld &&
            held.map((clip) => (
              <HeldCard
                key={clip.path}
                clip={clip}
                busy={busy === clip.path}
                onShare={() => void shareHeld(clip)}
                onDiscard={() => void discard(clip)}
                onRename={(title) => void rename(clip, title)}
                confirmDelete={preferences.confirm_delete}
              />
            ))}
          {items.map((clip, index) => (
            <ClipCard
              key={clip.clipId}
              clip={clip}
              onOpen={() => setSelectedIndex(index)}
              onCopy={() => copyLink(clip)}
              onTogglePin={() => handleTogglePin(clip)}
              onRename={(title) => void handleRename(clip, title)}
              onDelete={() => void handleDelete(clip)}
              confirmDelete={preferences.confirm_delete}
            />
          ))}
        </div>
      )}

      {hasMore && (
        <div className="more">
          <button className="ghost" onClick={loadMore} disabled={loading}>
            {loading ? 'Loading...' : 'Load more'}
          </button>
        </div>
      )}

      {selected && (
        <ClipDetail
          key={selected.clipId}
          clip={selected}
          hasPrev={selectedIndex !== null && selectedIndex > 0}
          hasNext={selectedIndex !== null && selectedIndex < items.length - 1}
          onClose={() => setSelectedIndex(null)}
          onPrev={() => setSelectedIndex((i) => (i === null ? null : Math.max(0, i - 1)))}
          onNext={() =>
            setSelectedIndex((i) => (i === null ? null : Math.min(items.length - 1, i + 1)))
          }
          onRename={(title) => handleRename(selected, title)}
          onTogglePin={() => handleTogglePin(selected)}
          onCopy={() => copyLink(selected)}
          onDelete={() => handleDelete(selected)}
          onTrim={(startMs, endMs) => handleTrim(selected, startMs, endMs)}
        />
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

/** The app mark, the same geometry branding.py draws for the tray and the
 *  executable: an A as an apex, with the counter and the gap between its legs
 *  cut back out. One mark, so the window and the taskbar agree. */
function BrandMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 100 100" aria-hidden="true">
      <defs>
        <linearGradient id="mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f8607a" />
          <stop offset="100%" stopColor="#ffb347" />
        </linearGradient>
      </defs>
      <path
        fill="url(#mark)"
        fillRule="evenodd"
        d="M50 11L94 89L6 89Z M50 37L64.8 60L35.2 60Z M35.2 72L64.8 72L73.5 89L26.5 89Z"
      />
    </svg>
  )
}

function HeldCard({
  clip,
  busy,
  onShare,
  onDiscard,
  onRename,
  confirmDelete,
}: {
  clip: HeldClip
  busy: boolean
  onShare: () => void
  onDiscard: () => void
  onRename: (title: string) => void
  confirmDelete: boolean
}) {
  return (
    <article className="card held-card">
      <div className="thumb">
        {clip.thumb ? (
          <img src={clip.thumb} alt="" />
        ) : (
          <span className="ph">On this PC</span>
        )}
        <span className="dur">{formatDuration(clip.durationMs)}</span>
        <span className="badge-new">No link yet</span>
      </div>
      <div className="card-body">
        <EditableTitle
          value={clip.title}
          placeholder="Untitled clip"
          onSave={onRename}
        />
        <div className="card-sub">
          <span>{clip.capturedAt ? formatDate(clip.capturedAt) : 'Just now'}</span>
          <span>{(clip.bytes / 1_048_576).toFixed(1)} MB</span>
        </div>
        <div className="held-actions">
          <button className="primary" disabled={busy} onClick={onShare}>
            {busy ? 'Uploading...' : 'Generate link'}
          </button>
          <DeleteButton onDelete={onDiscard} confirm={confirmDelete} />
        </div>
      </div>
    </article>
  )
}

function ClipCard({
  clip,
  onOpen,
  onCopy,
  onTogglePin,
  onRename,
  onDelete,
  confirmDelete,
}: {
  clip: Clip
  onOpen: () => void
  onCopy: () => void
  onTogglePin: () => void
  onRename: (title: string) => void
  onDelete: () => void
  confirmDelete: boolean
}) {
  const thumb = thumbnailUrl(clip)
  return (
    <article className={`card${clip.favorite ? ' pinned' : ''}`}>
      <button className="thumb" onClick={onOpen} aria-label={`Open ${clip.title ?? 'clip'}`}>
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" />
        ) : (
          <span className="ph">{clip.status}</span>
        )}
        <span className="dur">{formatDuration(clip.durationMs)}</span>
      </button>
      <div className="card-body">
        <EditableTitle
          value={clip.title}
          placeholder="Untitled clip"
          onSave={onRename}
        />
        <div className="card-sub">
          <span>{formatDate(clip.capturedAt)}</span>
          <span>{clip.viewCount} views</span>
        </div>
        <div className="card-link">
          <input
            readOnly
            value={`/c/${clip.publicSlug}`}
            title={clip.shareUrl}
            onClick={(event) => event.currentTarget.select()}
          />
          <button className="icon" onClick={onCopy} title="Copy link">
            <CopyIcon />
          </button>
          <DeleteButton onDelete={onDelete} confirm={confirmDelete} />
        </div>
      </div>
      <div className="card-actions">
        <button className="icon" onClick={onTogglePin} title={clip.favorite ? 'Unpin' : 'Pin'}>
          {clip.favorite ? '\u2605' : '\u2606'}
        </button>
      </div>
    </article>
  )
}

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  )
}


/**
 * Two-step, because a stray click on a grid should not destroy a clip. The
 * second click confirms, and moving the pointer away or waiting cancels it.
 */
function DeleteButton({
  onDelete,
  confirm,
}: {
  onDelete: () => void
  confirm: boolean
}) {
  const [armed, setArmed] = useState(false)

  useEffect(() => {
    if (!armed) return
    const timer = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(timer)
  }, [armed])

  return (
    <button
      className={`icon${armed ? ' arming' : ''}`}
      title={armed ? 'Click again to delete' : 'Delete clip'}
      // Only meaningful while a second click is expected.
      aria-live={confirm ? 'polite' : undefined}
      onMouseLeave={() => setArmed(false)}
      onClick={() => {
        if (armed || !confirm) onDelete()
        else setArmed(true)
      }}
    >
      {armed ? 'Sure?' : <TrashIcon />}
    </button>
  )
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
    </svg>
  )
}
