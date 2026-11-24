# Projection reads changed files only

## Goal

Make a projection's cost follow the number of changed memory files, not the store size.
Today every projection reads every truth file twice: once to hash it for the index, once
to parse it for MEMORY.md. A sleep reads every file again in each Manage pass. Behind an
on-access scanner that costs tens of seconds per pass on a store of about seven thousand
files.

## Units

1. Update the batch-write design contract: projection detects change by file signature
   and reads only files whose signature differs from the one it last indexed.
2. Add failing tests:
   - a projection after one write reads only the written file;
   - a change that keeps size and restores modification time is still detected;
   - an index without signatures, from an older version, still syncs correctly;
   - repeated record listing in one process reads each unchanged file once;
   - a sleep's file reads stay flat as passes repeat.
3. Record a file signature (size, inode, modification and change time) beside the content
   hash in the index manifest, migrating existing indexes. Reuse the known hash when the
   signature matches.
4. Cache parsed records in Store by path and signature, so repeated listings in one
   process reread only changed files.
5. Run targeted tests, the full suite, lint and types; measure a projection on the real
   store; commit, push to the fork and open a PR stacked on the batched-projection PR.

## Acceptance

- Projection and listing results are identical to a full read for every fixture.
- A content change is always detected, including one that restores modification time.
- Deleting the index still loses nothing; a missing or old signature means "read the file".

## Trade-off

Change time cannot be set by ordinary tools, so a restored modification time does not hide
an edit. A filesystem that reports coarse or no change time falls back to modification time
and size, the same trade-off Git makes for its index.

MEMORY.md is now rendered from the search index, which holds only records that pass
validation; a file that fails validation no longer appears in it.

## Verification

- Five of the eight new tests fail on the previous code; the three detection tests pass on
  both. Full suite: 728 passed, 1 skipped.
- Copy of a real store (7,117 files, on-access scanner): one write in a fresh process takes
  1.4 s and a second write 0.2 s, against roughly a minute or more before. The first sync
  on an index without signatures reads everything once.
