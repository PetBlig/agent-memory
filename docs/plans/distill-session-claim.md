# Distill session claim

## Goal

Stop overlapping distillations of one session. Each boundary launches a distillation; when
the previous one for the same session is still running, both read the same backlog, ask the
reasoner twice, and hold the writer lock in turn. On a slow store they pile up long enough to
time out a sleep.

## Units

1. Update the host adapter design: a distillation claims its session; a boundary that finds
   the session claimed only archives, and the claimant catches up before releasing it.
2. Add failing tests:
   - a distillation of a claimed session skips it and reports why, without asking the reasoner;
   - material archived while a distillation runs is distilled by that run before it ends;
   - catch-up stops after the configured number of rounds;
   - claims of different sessions do not block each other;
   - a claim is released when its distillation fails.
3. Add a non-blocking per-session claim beside the writer lock, with its directory and the
   catch-up round limit in the write configuration.
4. Wrap each session's distillation in the claim and repeat it while new backlog remains,
   up to the limit.
5. Run targeted tests, the full suite, lint and types; commit, push to the fork, open a PR.

## Acceptance

- Two concurrent distillations of one session never both ask the reasoner.
- No archived message is left undistilled because its boundary found the session claimed.
- A crashed distillation never leaves a session claimed: the claim is an advisory lock
  released by the operating system with the process.
