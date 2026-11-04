/** Backend URLs and public page settings. Public by design: the browser holds no secrets. */
export const BACKEND_WS = process.env.NEXT_PUBLIC_BACKEND_WS ?? "ws://localhost:8787/call";
export const BACKEND_HTTP = process.env.NEXT_PUBLIC_BACKEND_HTTP ?? "http://localhost:8787";
/** Shown on the outside-live-hours page, e.g. "7–10 PM IST, Nov 9–11". Empty: not announced. */
export const LIVE_HOURS = process.env.NEXT_PUBLIC_LIVE_HOURS ?? "";
/** A recorded real call to play outside live hours (Vahant records it). Empty: none yet. */
export const RECORDED_CALL_URL = process.env.NEXT_PUBLIC_RECORDED_CALL_URL ?? "";
