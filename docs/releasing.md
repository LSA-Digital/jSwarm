# Release notes and publication

## One source of truth

[`CHANGELOG.md`](../CHANGELOG.md) is the public core's version history. Update it
in the same pull request as a user-visible change. Write for someone deciding
whether to upgrade, not someone reading a commit log. Enterprise maintains its
own notes; do not imply that public core updates install Enterprise features.

Keep the next version explicitly marked `candidate (not released)` until
publication. Commits on main are not releases. Never retag an existing release
or relabel a mixed main checkout as an older version.

Every new version entry uses these headings, with `None` where appropriate:

- **Added:** new features and commands, including opt-in requirements.
- **Improved:** changed behavior and usability.
- **Fixed:** what was wrong and what now works.
- **Breaking changes:** compatibility changes, removed commands, and prerequisites.
- **Upgrade steps:** user actions, migrations, restarts, and recovery links.
- **Known limitations:** unsupported paths and unfinished acceptance checks.
- **Verification:** what was actually exercised, separate from manual work still open.

Keep bullets concise. Link longer guides instead of copying them. Do not include
private customer data, ticket keys, credentials, or transcripts.

## For every change

1. Update the candidate notes for product changes. Documentation-only fixes need
   a note when they materially change installation, recovery, or supported behavior.
2. Update the relevant usage/install guide, command contract, and tests together.
3. Run `.venv/bin/python -m jswarm.release_notes` from the clone. CI also checks
   that changes to product directories or installation dependencies include a
   changelog edit in the same push or pull request. It checks presence, not truth;
   the reviewer must check completeness and accuracy.
4. Keep the website's version links and upgrade instructions aligned. Its dated
   development history is supplementary, not a substitute for versioned notes.

## Before publishing a version

Retain the acceptance checks used for the first release. Record the exact commit,
OS, Claude Code version, and evidence location; re-run affected steps if code changes:

- Prove the tracker-free lifecycle through plan, build, test, a human browser UAT
  round, a bounded fix when needed, close, and merge. Inspect the merged result.
- For Jira, authenticate Atlassian, read a real issue, post the lifecycle comment,
  and read back the final transition. Pending requests are not observed results.
- Add optional ColGREP after installation and return a real search result. Exercise
  dry runs, upgrade, unadopt, and uninstall in a scratch installation without
  changing the real Claude configuration.
- Include portal build smoke tests and a full-history secret scan. Review findings
  before publication. Check setup, recovery, security, pricing, and download links
  against the same commit. Keep Enterprise private and separately scoped.

1. Agree on the exact version and release commit. Review the diff from the previous
   tag; account for new, changed, fixed, and removed behavior in the notes.
2. Run the full CI suite and leak gate against that commit. Record manual acceptance
   evidence and remaining limitations. Do not turn planned tests into passed claims.
3. Set the version heading and publication date. Check all version-specific install
   links and ensure release notes describe only code in this release.
4. With publication approval, create an annotated `vX.Y.Z` tag at the reviewed
   commit and publish a GitHub Release. Use the same version's changelog content
   as the release body, with working absolute documentation links and a comparison
   to the previous tag. Do not use generated commit titles as the entire release notes.
5. Verify the remote tag resolves to the intended commit and the Release is
   published, not a draft. Update the website's published-version card and verify
   it live. Keep unreleased main work clearly separate.
6. Start the next candidate entry with the required headings. Do not rewrite a
   historical release to include features delivered afterward.

Verify an unauthenticated clone and public download links. For reproducible installs,
select the release tag before installation; inspect local changes before switching.
The install lock records the checkout's Git version. A dirty or untagged checkout
is not a stable release. Enterprise requires a stable public version of at least
v1.0.0 and its own acceptance evidence. Archive release evidence and limitations.

`/jUpgrade` currently follows main. It retrieves the offered commit's changelog
before source approval, shows changes in the notes since the current checkout,
and still requires installer-preview approval. It does not silently switch users
between a tagged release and the development channel.
