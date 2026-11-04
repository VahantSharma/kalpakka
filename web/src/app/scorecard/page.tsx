"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ScorecardView } from "@/components/ScorecardView";
import { BACKEND_HTTP } from "@/lib/env";
import { decodeScorecard } from "@/lib/scorecard/decode";
import type { Scorecard } from "@/lib/scorecard/types";

/** A stored call's scorecard: /scorecard?id=<call id> -> GET /calls/{id}/scorecard. */
function StoredScorecard() {
  const q = useSearchParams();
  const id = q.get("id") ?? "";
  const name = q.get("name"); // the scorecard has no persona field; the share link carries it
  const [state, setState] = useState<{ card: Scorecard } | { error: string } | null>(null);
  useEffect(() => {
    if (!id) return;
    let live = true;
    fetch(`${BACKEND_HTTP}/calls/${encodeURIComponent(id)}/scorecard`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = decodeScorecard(await r.json());
        if (live) setState(d.ok ? { card: d.card } : { error: d.error });
      })
      .catch((e: unknown) => live && setState({ error: String(e) }));
    return () => {
      live = false;
    };
  }, [id]);
  if (!id) return <p>No call id given.</p>;
  if (!state) return <p>Loading scorecard…</p>;
  if ("error" in state) return <p role="alert">This scorecard could not be loaded ({state.error}).</p>;
  return <ScorecardView card={state.card} borrower={name} />;
}

export default function Page() {
  return (
    <main>
      <Suspense>
        <StoredScorecard />
      </Suspense>
    </main>
  );
}
