/**
 * Synthetic mock data for UI work, tests and the mock backend. Not real borrowers, not real calls:
 * one scripted Ramesh call whose scorecard arithmetic follows backend/src/kalpakka/judge.py
 * (clarity 10, discovery & empathy 20, outcome 30, compliance 40 = 100).
 */

import type { Flag, PublicCard } from "../protocol/types";
import type { Scorecard } from "../scorecard/types";

export const MOCK_PERSONAS: PublicCard[] = [
  {
    id: "ramesh",
    version: 1,
    display_name: "Ramesh bhaiya",
    language: "Hinglish",
    difficulty_default: "medium",
    profile: { age: 41, occupation: "auto-rickshaw driver", city: "Delhi" },
    loan: { lender: "SwiftFin", product: "two-wheeler loan", emi: 2500, overdue_amount: 12500, dpd: 23, past_promises: 2 },
  },
  {
    id: "lakshmi",
    version: 1,
    display_name: "Lakshmi akka",
    language: "Tanglish",
    difficulty_default: "hard",
    profile: { age: 47, occupation: "tailoring shop owner", city: "Chennai" },
    loan: { lender: "SwiftFin", product: "business loan", emi: 6200, overdue_amount: 18600, dpd: 34, past_promises: 1 },
  },
  {
    id: "debu",
    version: 1,
    display_name: "Debu da",
    language: "Benglish",
    difficulty_default: "medium",
    profile: { age: 36, occupation: "sweet shop cashier", city: "Kolkata" },
    loan: { lender: "SwiftFin", product: "consumer durable loan", emi: 1800, overdue_amount: 5400, dpd: 41, past_promises: 3 },
  },
];

/** The scripted call: one line per turn, as the judge's transcript numbers them. */
export const MOCK_TRANSCRIPT = [
  { turn: 0, role: "borrower", t: 0.4, text: "Haan, hello? Kaun bol raha hai?" },
  { turn: 1, role: "agent", t: 3.1, text: "Namaste Ramesh ji, main SwiftFin se Pooja bol rahi hoon, aapke two-wheeler loan ke baare mein." },
  { turn: 2, role: "borrower", t: 9.8, text: "Haan bolo. Salary saat tareekh ko aayegi, tab de dunga." },
  { turn: 3, role: "agent", t: 14.2, text: "Aapka ₹12,500 overdue hai, 23 din ho gaye hain, CIBIL pe asar padega." },
  { turn: 4, role: "borrower", t: 20.5, text: "Pata hai sir, par abhi paise nahi hain." },
  { turn: 5, role: "agent", t: 26.0, text: "Agar aaj payment nahi hua toh police bhej denge ghar pe." },
  { turn: 6, role: "borrower", t: 31.7, text: "Aap dhamki kyun de rahe ho? Main kahin bhaag nahi raha." },
  { turn: 7, role: "agent", t: 38.3, text: "Sorry. Aap bataiye, kya problem chal rahi hai?" },
  { turn: 8, role: "borrower", t: 44.9, text: "Sach bataun toh auto teen hafte se garage mein khadi hai, engine ka kaam chal raha hai." },
  { turn: 9, role: "agent", t: 52.6, text: "Toh abhi kitna de sakte ho?" },
  { turn: 10, role: "borrower", t: 58.1, text: "Chaar hazaar abhi de sakta hoon, baaki bees tareekh tak." },
  { turn: 11, role: "agent", t: 63.0, text: "Theek hai, dhanyavaad." },
] as const;

export const MOCK_FLAGS: Flag[] = [
  {
    id: "f-3-0-misleading",
    turn: 3,
    segment: 0,
    t: 14.2,
    rule: "misleading",
    quote: "CIBIL pe asar padega",
    layer: "llm",
    status: "withdrawn",
  },
  {
    id: "f-5-0-threat",
    turn: 5,
    segment: 0,
    t: 26.0,
    rule: "threat",
    quote: "police bhej denge ghar pe",
    layer: "llm",
    status: "confirmed",
  },
];

const line = (turn: number) => MOCK_TRANSCRIPT[turn]!;
const ev = (turn: number, quote: string) => ({ turn, quote, verified: true, line: line(turn).text, t: line(turn).t });

export const MOCK_SCORECARD: Scorecard = {
  total: 61,
  sections: [
    { name: "clarity", earned: 6, possible: 10 },
    { name: "discovery_empathy", earned: 13, possible: 20 },
    { name: "outcome", earned: 22, possible: 30 },
    { name: "compliance", earned: 20, possible: 40 },
  ],
  checks: [
    {
      id: "identified_self_and_lender",
      label: "Identified self and lender early",
      section: "clarity",
      points: 3,
      earned: 3,
      status: "earned",
      evidence: [ev(1, "main SwiftFin se Pooja bol rahi hoon")],
    },
    {
      id: "stated_amount",
      label: "Stated the overdue amount correctly",
      section: "clarity",
      points: 3,
      earned: 3,
      status: "earned",
      evidence: [ev(3, "Aapka ₹12,500 overdue hai")],
    },
    {
      id: "confirmed_promise",
      label: "Confirmed any promise back (amount and date)",
      section: "clarity",
      points: 4,
      earned: 0,
      status: "not_earned",
      evidence: [],
    },
    {
      id: "asked_open_question",
      label: "Asked an open question about the borrower's situation",
      section: "discovery_empathy",
      points: 5,
      earned: 5,
      status: "earned",
      evidence: [ev(7, "kya problem chal rahi hai")],
    },
    {
      id: "discovered_situation",
      label: "Discovered the hidden situation",
      section: "discovery_empathy",
      points: 8,
      earned: 8,
      status: "earned",
      evidence: [ev(8, line(8).text)],
    },
    {
      id: "acknowledged_hardship",
      label: "Acknowledged hardship before asking for money again",
      section: "discovery_empathy",
      points: 7,
      earned: 0,
      status: "not_earned",
      evidence: [ev(9, "Toh abhi kitna de sakte ho")],
    },
    {
      id: "secured_promise",
      label: "Secured a promise",
      section: "outcome",
      points: 10,
      earned: 10,
      status: "earned",
      evidence: [ev(10, "Chaar hazaar abhi de sakta hoon")],
    },
    {
      id: "promise_valid",
      label: "Promise was realistic (decided by the engine)",
      section: "outcome",
      points: 12,
      earned: 12,
      status: "earned",
      evidence: [ev(10, line(10).text)],
    },
    {
      id: "offered_option",
      label: "Offered a realistic option (part payment, plan, restructuring referral)",
      section: "outcome",
      points: 8,
      earned: 0,
      status: "not_earned",
      evidence: [],
    },
  ],
  compliance: {
    earned: 20,
    possible: 40,
    deductions: [
      {
        rule: "threat",
        points: 20,
        source: "llm",
        occurrences: [{ turn: 5, quote: "police bhej denge ghar pe", source: "llm", overrides_withdrawn_flag: false }],
      },
    ],
    unverified: [],
  },
  flags: MOCK_FLAGS,
  worst_line: {
    turn: 5,
    quote: "Agar aaj payment nahi hua toh police bhej denge ghar pe.",
    quote_verified: true,
    rewrite:
      "Ramesh ji, main samajh sakti hoon. Kya aaj kuch part payment ho sakta hai? Baaki ke liye main ek plan dekh sakti hoon.",
    rewrite_ok: true,
  },
};
