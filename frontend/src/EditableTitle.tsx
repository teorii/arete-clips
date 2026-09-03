import { useEffect, useRef, useState } from 'react'

interface Props {
  value: string | null
  placeholder: string
  onSave: (title: string) => void
}

/**
 * A title you can click and retype.
 *
 * Renaming lives on the card rather than only inside the player, because the
 * name a clip is given automatically is a starting point: you rename it while
 * looking at the grid, deciding which one to share.
 */
export function EditableTitle({ value, placeholder, onSave }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) input.current?.select()
  }, [editing])

  function begin(event: React.MouseEvent) {
    // The card opens the clip when clicked; renaming must not do both.
    event.stopPropagation()
    setDraft(value ?? '')
    setEditing(true)
  }

  function commit() {
    setEditing(false)
    const next = draft.trim()
    if (next !== (value ?? '')) onSave(next)
  }

  if (!editing) {
    return (
      <button className="card-title" onClick={begin} title="Click to rename">
        {value || placeholder}
      </button>
    )
  }

  return (
    <input
      ref={input}
      className="card-rename"
      value={draft}
      placeholder={placeholder}
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit()
        if (event.key === 'Escape') setEditing(false)
      }}
    />
  )
}
