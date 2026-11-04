"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { CallScreen } from "@/components/CallScreen";
import type { Difficulty } from "@/lib/protocol/types";

const DIFFICULTIES: readonly Difficulty[] = ["easy", "medium", "hard"];

function CallPage() {
  const q = useSearchParams();
  const persona = q.get("persona") ?? "ramesh";
  const d = q.get("difficulty");
  const difficulty: Difficulty = DIFFICULTIES.includes(d as Difficulty) ? (d as Difficulty) : "medium";
  const name = q.get("name") ?? "Ramesh bhaiya";
  return <CallScreen persona={{ id: persona, display_name: name }} difficulty={difficulty} />;
}

export default function Page() {
  return (
    <main>
      <Suspense>
        <CallPage />
      </Suspense>
    </main>
  );
}
