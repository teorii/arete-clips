import { useEffect, useState } from 'react'

import { DEFAULT_PREFERENCES, type Preferences, getBridge } from './bridge'

/**
 * The handful of preferences that change how the library behaves.
 *
 * Starts from the defaults so the page is usable before the bridge answers,
 * and stays on them in a plain browser where there is no bridge to ask.
 */
export function usePreferences(): Preferences {
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES)

  useEffect(() => {
    let live = true
    void getBridge().then(async (bridge) => {
      if (!bridge) return
      try {
        const current = await bridge.preferences()
        if (live) setPreferences({ ...DEFAULT_PREFERENCES, ...current })
      } catch {
        // Defaults are already in place, so there is nothing to recover from.
      }
    })
    return () => {
      live = false
    }
  }, [])

  return preferences
}
