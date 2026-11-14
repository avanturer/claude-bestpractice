# Watch Mode

Once a teardown exists, rerun it monthly and report only what's new.

1. Re-run `stack`, `history`, `hero`, `site`, `dns`, `subdomains`, `jobs`, and `reviews`, plus the industry pack's commands (`shopify`, `cfpb`, `fdic`, `filings`, `adoption`), check the ad libraries and paid promotion, and pull the last month's posts and new interviews. Append new dated moves to a fresh `moves.csv`.
2. Diff the new log against the old one:
   ```bash
   node scripts/teardown.mjs diff --old teardowns/target/moves-2026-09.csv --new teardowns/target/moves.csv --out teardowns/target/new-moves.csv
   ```
3. Report only meaningful new moves: a new ad network, a new page family, a new loop, a pricing change, a new content format, a burst of hiring in one channel, a new complaint theme.

   **Launch radar:** when two or more of these land within a few weeks (a new subdomain, a trademark filing, new job titles in one area, a sitemap jump, a new domain verification), flag a likely launch and say what it points to. Write "no new moves" when there are none, which is the usual result.
4. For each new move, say whether it changes a play and which skill to hand it to.

To schedule it, run it as the growth-move watch loop in **marketing-loops**, keeping the dated move logs as its state.
