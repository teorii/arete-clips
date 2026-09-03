/**
 * The links you copied recently, kept in this browser.
 *
 * Pinning lives in the database because it is a property of the clip. This is
 * a different thing: a per-browser trail of what you actually shared, so a link
 * pasted into Discord ten minutes ago is one click away without hunting for the
 * clip it came from.
 */

export interface RecentLink {
  slug: string
  url: string
  title: string
  copiedAt: string
}

const KEY = 'clipper.recent-links'
const LIMIT = 12

export function loadRecent(): RecentLink[] {
  // Storage throws outright in some privacy modes, so never let it break render.
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as RecentLink[]) : []
  } catch {
    return []
  }
}

export function pushRecent(entry: Omit<RecentLink, 'copiedAt'>): RecentLink[] {
  const next: RecentLink[] = [
    { ...entry, copiedAt: new Date().toISOString() },
    ...loadRecent().filter((r) => r.slug !== entry.slug),
  ].slice(0, LIMIT)
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* over quota or blocked: the in-memory list still updates */
  }
  return next
}

export function clearRecent(): RecentLink[] {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* nothing to do */
  }
  return []
}
