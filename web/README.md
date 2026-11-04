# KalPakka web

Next.js (App Router) + TypeScript strict. The browser is a microphone, a speaker and a screen:
it holds no secrets, no persona hidden fields and no scoring logic. Everything comes from the
backend over one WebSocket (`/call`) and a few REST reads.

## Commands

```
npm ci            # install from package-lock.json (exact versions)
npm run dev       # mock backend on :8787 + Next dev server on :3000
npm run dev:web   # Next only (point it at a real backend with the env vars below)
npm run mock      # mock backend only
npm run build     # production build
npm run lint      # eslint + tsc --noEmit
npm run test      # vitest
```

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `NEXT_PUBLIC_BACKEND_WS` | `ws://localhost:8787/call` | call WebSocket (must be `wss://` on an HTTPS page) |
| `NEXT_PUBLIC_BACKEND_HTTP` | `http://localhost:8787` | REST base (`/personas`, `/calls/{id}/scorecard`, ...) |

The defaults point at the mock backend in `mock/`, which speaks the protocol in
`docs/context/05-architecture.md` with canned, clearly-labelled mock content (tones instead of
speech, scripted transcript lines). It is for UI work and tests only.

## Lockfile note

`package-lock.json` was generated with npm 11: npm 10.9's resolver crashes
(`Cannot read properties of null (reading 'edgesOut')`) on vitest 4/5's peer set. `npm ci` with
npm 10 installs from the lockfile without hitting that code path (verified).
