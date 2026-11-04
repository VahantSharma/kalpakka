# 02 · Loan recovery in India, from zero

You cannot build a good recovery simulator without knowing what a recovery agent is trying to do,
under what pressure, and within what rules. One running example: Ramesh's two-wheeler loan.

## A loan, an EMI, a bounce

Ramesh, an auto driver in Delhi, borrows from **SwiftFin** (fictional NBFC) and repays in **EMIs**
(equated monthly instalments) due on the 5th. The EMI is debited automatically through an
**e-mandate / NACH** mandate. If the account is short, the debit **bounces**, a **bounce charge**
may apply, and the account is **overdue**.

## DPD, SMA and NPA

From the first unpaid day the lender counts **DPD (days past due)**:

| Status | Days overdue | Meaning |
|--------|-------------|---------|
| SMA-0 | up to 30 | Early stress; often one missed or bounced EMI |
| SMA-1 | 31–60 | Real stress; usually a second EMI missed |
| SMA-2 | 61–90 | Serious; one step from default |
| NPA | over 90 | Non-performing asset; the loan has officially gone bad |

SMA/NPA status hits the borrower's credit report (CIBIL) and forces the lender to set money aside
(provisioning). Collections teams group accounts by DPD into **buckets** and fight **roll rates**
(the share sliding to the next bucket).

## What happens at each stage (typical retail lending)

1. **Pre-due:** reminder calls/SMS/WhatsApp. Cheap, polite, high volume — where voice bots shine.
2. **Early bucket (1–30 DPD):** soft collection; the goal is a **PTP (promise to pay)**: a specific
   amount on a specific date.
3. **Mid buckets (31–90):** human tele-callers negotiate, often at outsourced **collection agencies**;
   field visits start. Excuses, disputes, real hardship, anger.
4. **Late (90+, NPA):** field recovery, settlements, legal notices, repossession for secured loans.

**Ramesh's story:** his EMI bounces on the 5th because a passenger's UPI payment did not arrive. A
bot calls on the 6th; he says "kal pakka"; nothing is paid. On the 20th (still SMA-0, 23 DPD by
the time we meet him) a human tele-caller calls. That call can go right (agent learns the auto was
off the road and agrees a part-payment) or wrong (threats of home visits, police, calling his
brother-in-law). **KalPakka trains the person who makes that call.**

## The chain of people

Borrower → lender (RBI calls them *regulated entities*) → collection agency → recovery agents
(young, new, short tenure, paid on recovery) → voice AI vendor (Gnani: bots, analytics, agent assist)
→ RBI, IIBF (certifies agents), grievance channels (lender's cell, RBI Ombudsman), credit bureaus.
**The lender stays accountable for what its agents do**, even several contracts away.

Why harassment happens: incentives paid on recovery, volume pressure, thin training and high churn,
distance between lender and caller, language mismatch. Complaints are large: 12,903 complaints
(Apr 2021–Nov 2022) on digital lending apps and recovery agents under RBI's Integrated Ombudsman
Scheme; about 13.34 lakh total Ombudsman complaints in 2024–25.

## What the rules say (and how KalPakka tests each)

RBI's Aug 2022 instructions: no intimidation or harassment, no recovery calls before 8 AM or after
7 PM, no threatening or anonymous calls, no intrusion into the privacy of family, friends, referees,
no false or misleading representations. The **revised directions finalised Aug 2026, effective
Jan 1 2027**, set common rules across lenders:

| Requirement | On a call | KalPakka test |
|-------------|-----------|---------------|
| Agents trained and IIBF-certified **before** contact | Training is mandatory and up-front | The product itself |
| Contact only 8:00 AM – 7:00 PM | Late-evening recovery calls are violations | `late_clock` trap (sim clock 7:20 PM) |
| No recovery contact during bereavement, weddings, festivals | Parent died → recovery pauses | `bereavement` trap |
| Communicate only with borrower or guarantor | Never discuss the debt with a spouse/neighbour/employer | `third_party_wife` trap |
| Harsh practices prohibited: abuse, threats, repeated pressure, social media misuse | Police, jail, "sending people home" | Live tripwire |
| No false or misleading statements | "CIBIL zero forever", "FIR today" | Misrepresentation checks |
| Pending grievances first | Resolve a complaint before recovery | `disputed_charge` (Lakshmi) |
| Calls recorded and documented | Every call auditable | Audit mode, same rubric |

IIBF's DRA certificate needs 50 hours of accredited training for graduates (100 hours on a Class 10
basis) and an exam. Many agents + a hard deadline + a big training requirement = the opening.

KalPakka is **not** legal advice and does not certify compliance. It teaches the principles.

## How Gnani's clients use voice AI today

From Gnani's published customer stories: bots take routine, high-volume calls; humans keep the hard
ones. KreditBee (pre/post-due calls in the borrower's language; collectors focus on negotiation;
46% lower collections cost reported), LazyPay (pre-due calls, PTPs written to CRM; hard cases to
humans), an auto-finance NBFC (routine calls, PTPs scheduled in-call; disputes, settlements and
hardship go to people). Stack in those stories: Gnani's agent platform with Prisma v2.5 and Timbre v2.5.

A collections bot call: dial → identify self and lender, confirm it is the borrower → state dues in
the borrower's language → ask why missed, classify reason → secure a PTP (often an SMS payment link)
→ handle simple objections or hand over → disposition code (PTP, refused, wrong number, call back).

**The gap KalPakka fills:** the hardest calls go to the least-practised people (Train); collections
bots are hard to test against difficult, emotional, code-mixed borrowers before launch (Arena);
quality checks happen after the harm (Audit, same rubric).

## Glossary

| Term | Meaning |
|------|---------|
| Bounce | A failed auto-debit of the EMI |
| Bounce / penal charge | Fee for a failed or late payment |
| Bucket | Overdue accounts grouped by DPD |
| CIBIL report | Credit bureau record lenders check |
| Collection agency | Outside firm paid to recover dues |
| Disposition | Call outcome code (PTP, refused, call back, wrong number) |
| DPD | Days past due |
| DRA | Debt recovery agent; IIBF's certificate for them |
| e-mandate / NACH | Standing instruction for automatic EMI debits |
| EMI | Equated monthly instalment |
| Grievance | A formal borrower complaint; pending grievances come before recovery |
| Harsh practices | RBI's term for abuse, threats, intimidation, repeated pressure |
| IIBF | Indian Institute of Banking and Finance |
| NPA | Non-performing asset (over 90 DPD) |
| PTP | Promise to pay: an amount and a date |
| Regulated entity (RE) | RBI's term for a regulated lender |
| Restructuring | Changing loan terms for a borrower in genuine hardship |
| Roll rate | Share of accounts sliding to a worse bucket |
| Settlement | Lender accepts less than the full dues |
| SMA-0/1/2 | Special mention account stages by DPD |
| Third-party disclosure | Revealing the debt to anyone but borrower/guarantor — prohibited |
