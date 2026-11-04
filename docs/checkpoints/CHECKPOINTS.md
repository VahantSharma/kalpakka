# Checkpoint ledger

A checkpoint is a point where everything up to it is verified to work as expected, recorded with
evidence and tagged in git by Vahant. Records live next to this file as `CP<n>-<slug>.md`
(template: `docs/templates/checkpoint.md`). Only `/checkpoint` adds rows, after the
`checkpoint-verifier` agent reports READY.

| CP | Name | Date (IST) | Commit | Git tag | Decision | Key numbers | Record |
|----|------|-----------|--------|---------|----------|-------------|--------|
| | | | | | | | |

Tag convention (Vahant runs): `git tag -a cp0-day1-kill-check -m "..."`, `cp1-ramesh-stays-ramesh`,
`cp2-first-voice-call`, `cp3-scored-call`, `cp4-feature-complete`, `cp5-release-candidate`, `cp6-live`,
`cp7-submitted`, and `v1.0` on the submitted commit.

To return to a checkpoint: `git checkout cp<n>-<slug>` (read-only look) or
`git switch -c fix/<name> cp<n>-<slug>` (branch from it).
