import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { adoptKeyFromUrl } from './api'
import './styles.css'

// Before render, so the app's first request already carries the key.
adoptKeyFromUrl()

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
