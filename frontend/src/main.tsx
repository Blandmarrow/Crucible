import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `0.0.0.0` is a bind address, not an address to browse to, but it is the last
// URL on screen at startup — both launchers print `http://localhost:8000` and
// uvicorn's own hyperlinked banner (`Uvicorn running on http://0.0.0.0:8000`)
// prints right after it. That origin is not a secure context, so Chromium never
// installs `crypto.randomUUID` or `navigator.clipboard` there and features built
// on them break (issue #93; PM-024). Bounce to the equivalent `localhost` URL,
// which is secure by fiat.
//
// The render is the `else` branch, not a statement after the `if`:
// `location.replace` *queues* a navigation and does not halt script execution, so
// falling through would mount the whole app — opening the global SSE stream,
// firing the initial queries, hydrating the persisted zustand stores — against
// `0.0.0.0` for the few milliseconds before the navigation lands. That origin has
// its own `localStorage`, separate from `localhost`'s, so anything written there
// is invisible once we arrive.
//
// Exactly this one hostname. A LAN IP like `192.168.1.5:8000` is insecure too,
// but it is a deliberate remote-access choice and rewriting it to `localhost`
// would point the browser at the wrong machine — that origin is covered by the
// feature-tested fallbacks instead. No conflict with the splash handover, whose
// `location.replace("/")` is relative and so keeps whatever host it is on.
if (location.hostname === '0.0.0.0') {
  location.replace(`${location.protocol}//localhost${location.port ? `:${location.port}` : ''}${location.pathname}${location.search}${location.hash}`)
} else {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
