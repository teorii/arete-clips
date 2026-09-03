import { useCallback, useEffect, useState } from 'react'

import {
  type Clip,
  copyText,
  deleteClip,
  formatDate,
  formatDuration,
  patchClip,
  thumbnailUrl,
} from './api'
import { ClipDetail } from './ClipDetail'
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
        <h1>Arete</h1>
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

      {error && (
        <div className="error">
          {error}
          <button className="ghost" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      {!loading && items.length === 0 && !error ? (
        <div className="empty">
          {search || filter === 'pinned'
            ? 'No clips match that.'
            : 'No clips yet. Run the capture daemon and press F9 in game.'}
        </div>
      ) : (
        <div className="grid">
          {items.map((clip, index) => (
            <ClipCard
              key={clip.clipId}
              clip={clip}
              onOpen={() => setSelectedIndex(index)}
              onCopy={() => copyLink(clip)}
              onTogglePin={() => handleTogglePin(clip)}
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
        />
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

function ClipCard({
  clip,
  onOpen,
  onCopy,
  onTogglePin,
}: {
  clip: Clip
  onOpen: () => void
  onCopy: () => void
  onTogglePin: () => void
}) {
  const thumb = thumbnailUrl(clip)
  return (
    <article className="card">
      <button className="thumb" onClick={onOpen} aria-label={`Open ${clip.title ?? 'clip'}`}>
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" />
        ) : (
          <span className="ph">{clip.status}</span>
        )}
        <span className="dur">{formatDuration(clip.durationMs)}</span>
      </button>
      <div className="card-body">
        <button className="card-title" onClick={onOpen}>
          {clip.title || 'Untitled clip'}
        </button>
        <div className="card-sub">
          <span>{formatDate(clip.capturedAt)}</span>
          <span>{clip.viewCount} views</span>
        </div>
      </div>
      <div className="card-actions">
        <button className="icon" onClick={onTogglePin} title={clip.favorite ? 'Unpin' : 'Pin'}>
          {clip.favorite ? '\u2605' : '\u2606'}
        </button>
        <button className="icon" onClick={onCopy} title="Copy link">
          <CopyIcon />
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
