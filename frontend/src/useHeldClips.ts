import { useCallback, useEffect, useState } from 'react'

import { type Bridge, type HeldClip, getBridge } from './bridge'

/**
 * Clips sitting on this machine with no link yet.
 *
 * Only populated inside the desktop app: in a browser there is no client to
 * ask, so `available` stays false and the library shows server clips alone.
 */
export function useHeldClips() {
  const [bridge, setBridge] = useState<Bridge | null>(null)
  const [held, setHeld] = useState<HeldClip[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(async (via: Bridge | null) => {
    if (!via) return
    try {
      setHeld(await via.held_clips())
    } catch {
      setHeld([])
    }
  }, [])

  useEffect(() => {
    let live = true
    void getBridge().then((found) => {
      if (!live || !found) return
      setBridge(found)
      void refresh(found)
    })
    return () => {
      live = false
    }
  }, [refresh])

  // The hotkey fires while this window is hidden, so clips appear without
  // anything here having caused them. Polling is how they show up.
  useEffect(() => {
    if (!bridge) return
    const timer = setInterval(() => void refresh(bridge), 3000)
    return () => clearInterval(timer)
  }, [bridge, refresh])

  const generate = useCallback(
    async (clip: HeldClip): Promise<{ ok: boolean; url?: string; message?: string }> => {
      if (!bridge) return { ok: false, message: 'Not running in the app' }
      setBusy(clip.path)
      try {
        return await bridge.generate_link(clip.path)
      } catch (err) {
        return { ok: false, message: String(err) }
      } finally {
        setBusy(null)
        void refresh(bridge)
      }
    },
    [bridge, refresh],
  )

  const discard = useCallback(
    async (clip: HeldClip) => {
      if (!bridge) return
      setBusy(clip.path)
      try {
        await bridge.discard_clip(clip.path)
      } finally {
        setBusy(null)
        void refresh(bridge)
      }
    },
    [bridge, refresh],
  )

  const rename = useCallback(
    async (clip: HeldClip, title: string) => {
      if (!bridge) return
      // Optimistic: the rename is local to this machine and cannot really
      // fail, and waiting on a round trip to redraw a title feels broken.
      setHeld((current) =>
        current.map((c) => (c.path === clip.path ? { ...c, title: title || null } : c)),
      )
      await bridge.rename_clip(clip.path, title)
      void refresh(bridge)
    },
    [bridge, refresh],
  )

  const discardAll = useCallback(async () => {
    if (!bridge) return
    setBusy('all')
    try {
      await bridge.discard_all_clips()
    } finally {
      setBusy(null)
      void refresh(bridge)
    }
  }, [bridge, refresh])

  // Held clips are the only thing this app stores on your own disk, and it
  // stores them until you say otherwise. Surfacing the total is what stops
  // that being a surprise.
  const heldBytes = held.reduce((total, clip) => total + (clip.bytes || 0), 0)

  return {
    held,
    heldBytes,
    busy,
    generate,
    discard,
    discardAll,
    rename,
    available: bridge !== null,
  }
}
