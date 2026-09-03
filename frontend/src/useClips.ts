import { useCallback, useEffect, useRef, useState } from 'react'

import { type Clip, listClips } from './api'

export type Filter = 'all' | 'pinned'

const PAGE_SIZE = 30

/**
 * Cursor-paginated clip list with filters.
 *
 * Responses are tagged with a request id so a slow reply from an abandoned
 * search cannot overwrite the results of a newer one.
 */
export function useClips(search: string, filter: Filter) {
  const [items, setItems] = useState<Clip[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const requestId = useRef(0)

  const load = useCallback(
    async (append: boolean, from: string | null) => {
      const id = ++requestId.current
      setLoading(true)
      try {
        const page = await listClips({
          cursor: from,
          limit: PAGE_SIZE,
          q: search || undefined,
          favorite: filter === 'pinned' ? true : undefined,
        })
        if (id !== requestId.current) return
        setItems((prev) => (append ? [...prev, ...page.items] : page.items))
        setCursor(page.nextCursor)
        setError(null)
      } catch (err) {
        if (id !== requestId.current) return
        setError(err instanceof Error ? err.message : 'Could not reach the clip service')
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    },
    [search, filter],
  )

  useEffect(() => {
    void load(false, null)
  }, [load])

  const loadMore = useCallback(() => {
    if (cursor && !loading) void load(true, cursor)
  }, [cursor, loading, load])

  const refresh = useCallback(() => void load(false, null), [load])

  const replace = useCallback((clip: Clip) => {
    setItems((prev) => prev.map((c) => (c.clipId === clip.clipId ? clip : c)))
  }, [])

  const remove = useCallback((clipId: string) => {
    setItems((prev) => prev.filter((c) => c.clipId !== clipId))
  }, [])

  return { items, loading, error, hasMore: cursor !== null, loadMore, refresh, replace, remove }
}
