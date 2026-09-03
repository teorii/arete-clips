/**
 * The desktop app's Python bridge.
 *
 * Clips captured on this machine are held locally until someone asks for a
 * link, so the server has never heard of them. A "Generate link" button
 * therefore has to reach the client, not the API, and this is the only channel
 * that does.
 *
 * Absent in an ordinary browser tab, where there is no client to ask, so every
 * caller has to cope with null.
 */

export interface HeldClip {
  path: string
  capturedAt: string | null
  durationMs: number
  bytes: number
  title: string | null
  /** Inline poster: a held clip has no URL to serve one from. */
  thumb: string | null
}

export interface Bridge {
  held_clips(): Promise<HeldClip[]>
  generate_link(path: string): Promise<{ ok: boolean; url?: string; message?: string }>
  discard_clip(path: string): Promise<{ ok: boolean; message?: string }>
  discard_all_clips(): Promise<{ ok: boolean; removed?: number; message?: string }>
  rename_clip(path: string, title: string): Promise<{ ok: boolean; message?: string }>
}

function current(): Bridge | null {
  const host = window as unknown as { pywebview?: { api?: Bridge } }
  return host.pywebview?.api ?? null
}

/**
 * pywebview injects its bridge after the page loads, so a check at import time
 * sees nothing even inside the app. Poll briefly, then give up.
 */
export async function getBridge(timeoutMs = 4000): Promise<Bridge | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const found = current()
    if (found) return found
    await new Promise((resolve) => setTimeout(resolve, 80))
  }
  return null
}
