# Publishing v1.0.0

The first public release is **v1.0.0**. A development checkout,
passing unit tests, or a successful installer is not full lifecycle acceptance.

## Before publication

1. Record the exact public commit exercised on the clean Mac, OS and Claude Code
   versions, and the evidence location. Re-run affected steps if that commit changes.
2. Prove the tracker-free path from install and adoption through `/jPlan`, `/jGo`,
   `/jTest`, a human browser UAT round, a bounded `/jFix` when needed, `/jClose`,
   and `/jMerge`. Verify the merged result in the application repository.
3. For the supported Jira path, authenticate Atlassian on that Mac. Read a known
   issue, post the lifecycle comment, and read back the final transition. Capture
   actual results, not pending host requests or locally authored assertions.
4. Confirm optional ColGREP can be added after install and returns a real search
   result. Verify recovery: dry runs, upgrade, unadopt, and uninstall on a scratch
   installation, without changing the user's real Claude configuration.
5. Require green CI, including the portal build smoke tests. Run the leak gate
   and a full-history secret scan. Review any finding before making history public.
6. Check website setup, recovery, security reporting, pricing boundaries, and
   download links against this exact commit. Keep Enterprise private and do not
   represent its unfinished setup as a released, self-serve product.

## Publication sequence

After the owner accepts the evidence, freeze the tested commit. Update the
changelog from unreleased to the real publication date. Create the annotated
`v1.0.0` tag and GitHub release on that commit; never move a published tag.
Make **only jSwarm** public. Verify an unauthenticated clone and the release link.
Then merge the website release branch and verify the production routes and
download links without a logged-in GitHub session. The website must not announce
available downloads while the source still returns a private-repository 404.

For reproducible installation, select the release tag in the jSwarm clone before
running the installer. Review any local edits before changing checkouts. The lock
file records the checkout's Git version; an untagged or dirty checkout is not a
stable release. Enterprise requires a stable public version of at least v1.0.0.

Archive the acceptance evidence and checks with the release. Leave known
limitations visible: macOS and Claude Code only; Jira optional; Enterprise
capabilities and rollout confirmed through a separate engagement.
