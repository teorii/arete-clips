/**
 * Typed client for the clip API.
 *
 * All paths are relative. In development Vite proxies /api to the FastAPI
 * server; in production the built app is served from that same server. Either
 * way there is one origin and no base URL to configure.
 */

export interface Rendition {
  label: string
  status: string
  bytes: number | null
  url: string | null
}

export interface Clip {
  clipId: string
  publicSlug: string
  shareUrl: string
  title: string | null
  status: string
  durationMs: number
  capturedAt: string
  uploadedAt: string | null
  viewCount: number
  favorite: boolean
  captureMeta: Record<string, unknown> | null
  renditions: Rendition[]
}

export interface ClipPage {
  items: Clip[]
  nextCursor: string | null
}

export interface ClipQuery {
  cursor?: string | null
  limit?: number
  q?: string
  favorite?: boolean
  status?: string
}

export class ApiError extends Error {
  // An explicit field rather than a constructor parameter property, which the
  // template's erasableSyntaxOnly setting disallows.
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const KEY_STORAGE = 'arete.api-key'

/**
 * Take an API key out of the URL fragment and remember it.
 *
 * The desktop app opens the window at `/app/#k=<key>`. A fragment is never sent
 * to the server, so the key stays out of access logs and referrers, and reading
 * it before the first render avoids racing the app's opening request. Call this
 * once, before rendering.
 */
export function adoptKeyFromUrl(): void {
  const match = /[#&]k=([^&]+)/.exec(window.location.hash)
  if (!match) return
  try {
    localStorage.setItem(KEY_STORAGE, decodeURIComponent(match[1]))
  } catch {
    /* storage blocked; the key still works for this page load below */
  }
  // Strip it so it is not sitting in the address bar to be copied by accident.
  history.replaceState(null, '', window.location.pathname + window.location.search)
}

export function apiKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) ?? ''
  } catch {
    return ''
  }
}

export function setApiKey(key: string): void {
  try {
    localStorage.setItem(KEY_STORAGE, key.trim())
  } catch {
    /* nothing useful to do */
  }
}

/** Something a person can act on.
 *
 * A failing request does not always answer in JSON: a proxy, a tunnel that has
 * expired, or a server that fell over answers in HTML, and pasting that into
 * the page put a whole error document across the top of the library. Nothing
 * in it told you what to do.
 */
function readableError(body: string, response: Response): string {
  const trimmed = body.trim()

  if (trimmed.startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(trimmed)
      const detail = (parsed as { detail?: unknown }).detail
      if (typeof detail === 'string' && detail) return detail
    } catch {
      /* not the JSON it looked like; fall through to the status */
    }
  }

  if (trimmed && !trimmed.startsWith('<')) return trimmed.slice(0, 200)

  if (response.status === 404) return 'The server did not recognise that address.'
  if (response.status === 401 || response.status === 403) {
    return 'This machine is not signed in to that library.'
  }
  if (response.status >= 500) return 'The server failed to answer. It may have stopped.'
  return response.statusText || `Request failed (${response.status}).`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const key = apiKey()
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-API-Key': key } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ApiError(readableError(body, response), response.status)
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

export function listClips(query: ClipQuery = {}): Promise<ClipPage> {
  const params = new URLSearchParams()
  if (query.cursor) params.set('cursor', query.cursor)
  if (query.limit) params.set('limit', String(query.limit))
  if (query.q) params.set('q', query.q)
  if (query.favorite !== undefined) params.set('favorite', String(query.favorite))
  if (query.status) params.set('status', query.status)
  const qs = params.toString()
  return request<ClipPage>(`/api/clips${qs ? `?${qs}` : ''}`)
}

export function patchClip(
  clipId: string,
  changes: { title?: string | null; favorite?: boolean; visibility?: string },
): Promise<Clip> {
  return request<Clip>(`/api/clips/${clipId}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })
}

export function deleteClip(clipId: string): Promise<void> {
  return request<void>(`/api/clips/${clipId}`, { method: 'DELETE' })
}

// ------------------------------------------------------------------ helpers

export function thumbnailUrl(clip: Clip): string | null {
  return clip.renditions.find((r) => r.label === 'thumb' && r.url)?.url ?? null
}

export function sourceUrl(clip: Clip): string | null {
  return clip.renditions.find((r) => r.label === 'source' && r.url)?.url ?? null
}

export function sourceBytes(clip: Clip): number | null {
  return clip.renditions.find((r) => r.label === 'source')?.bytes ?? null
}

export function formatDuration(ms: number): string {
  const total = Math.round(ms / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0 ? `${minutes}:${String(seconds).padStart(2, '0')}` : `${seconds}s`
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return '-'
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Put text on the clipboard, with a fallback.
 *
 * The async clipboard API needs a secure context and user activation, and it
 * rejects in more situations than you would expect. The deprecated execCommand
 * path still works behind a real click, so try it before giving up.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const scratch = document.createElement('textarea')
      scratch.value = text
      scratch.setAttribute('readonly', '')
      scratch.style.position = 'fixed'
      scratch.style.opacity = '0'
      document.body.appendChild(scratch)
      scratch.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(scratch)
      return ok
    } catch {
      return false
    }
  }
}

/**
 * Shorten a clip in place.
 *
 * Destructive: the trimmed-away seconds stop existing. Cuts land on the nearest
 * earlier keyframe, so the result can be slightly wider than requested.
 */
export function trimClip(clipId: string, startMs: number, endMs: number): Promise<Clip> {
  return request<Clip>(`/api/clips/${clipId}/trim`, {
    method: 'POST',
    body: JSON.stringify({ startMs: Math.round(startMs), endMs: Math.round(endMs) }),
  })
}

/** Keyframe spacing in ms, which is how coarse a cut can be. */
export function keyframeGridMs(clip: Clip): number {
  const seconds = Number(clip.captureMeta?.segment_seconds ?? 2)
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 2000
}

export function formatClock(ms: number): string {
  const total = Math.max(0, ms) / 1000
  const minutes = Math.floor(total / 60)
  const seconds = (total % 60).toFixed(1).padStart(4, '0')
  return `${minutes}:${seconds}`
}
