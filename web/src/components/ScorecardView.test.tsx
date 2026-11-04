// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MOCK_SCORECARD } from "@/lib/mock/fixtures";
import { ScorecardView } from "./ScorecardView";

afterEach(cleanup);

describe("ScorecardView", () => {
  it("renders the total and the four sections with their scores", () => {
    render(<ScorecardView card={MOCK_SCORECARD} borrower="Ramesh bhaiya" />);
    expect(screen.getByRole("heading", { name: /Your call with Ramesh bhaiya/ })).toBeTruthy();
    expect(screen.getByText("61")).toBeTruthy();
    for (const [name, score] of [
      ["Outcome", "22 / 30"],
      ["Compliance", "20 / 40"],
      ["Discovery & empathy", "13 / 20"],
      ["Clarity", "6 / 10"],
    ]) {
      const h = screen.getByRole("heading", { name: new RegExp(`^${name}`) });
      expect(h.textContent).toContain(score);
    }
  });

  it("shows each mark's transcript line with its timestamp", () => {
    render(<ScorecardView card={MOCK_SCORECARD} borrower="Ramesh bhaiya" />);
    const outcome = screen.getByRole("heading", { name: /^Outcome/ }).closest("section")!;
    expect(within(outcome).getAllByText(/Chaar hazaar abhi de sakta hoon, baaki bees tareekh tak/)).toHaveLength(2);
    const promise = within(outcome).getByText("Secured a promise").closest("li")!;
    expect(within(promise).getByText("0:58")).toBeTruthy();
  });

  it("shows the threat deduction and the withdrawn flag with its note", () => {
    render(<ScorecardView card={MOCK_SCORECARD} borrower="Ramesh bhaiya" />);
    const comp = screen.getByRole("heading", { name: /^Compliance/ }).closest("section")!;
    expect(within(comp).getByText("−20")).toBeTruthy();
    const flags = screen.getByRole("heading", { name: /Flags during the call/ }).closest("section")!;
    expect(within(flags).getByText(/on reflection, this was fine/)).toBeTruthy();
    expect(within(flags).getByText("confirmed")).toBeTruthy();
  });

  it("does not claim the trainee said an unverified worst line", () => {
    const card = { ...MOCK_SCORECARD, worst_line: { ...MOCK_SCORECARD.worst_line!, quote_verified: false } };
    render(<ScorecardView card={card} borrower="Ramesh bhaiya" />);
    expect(screen.queryByText(/You said:/)).toBeNull();
    expect(screen.getByText(/The judge cited:/)).toBeTruthy();
  });

  it("disables the play button when there is no audio for the better line", () => {
    render(<ScorecardView card={MOCK_SCORECARD} borrower="Ramesh bhaiya" />);
    const btn = screen.getByRole("button", { name: /Hear it/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/Try: “Ramesh ji, main samajh sakti hoon/)).toBeTruthy();
  });

  it("enables the play button when audio exists and the rewrite passed the rules check", () => {
    render(<ScorecardView card={MOCK_SCORECARD} borrower="Ramesh bhaiya" betterLineUrl="/x.wav" />);
    expect((screen.getByRole("button", { name: /Hear it/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});
