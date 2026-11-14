# Stage 2: Read the Funnel

Walk the target's funnel one stage at a time. Each finding goes in the write-up, and each dated one goes in `moves.csv`.

### Acquisition

| Channel | What to collect |
|---|---|
| **Search** | Ranked keywords by URL, from the user's SEO tool (DataForSEO, Semrush, Ahrefs, or Similarweb; without one, only page families from `site` are available). Map them to the page families from `site`. Report brand vs non-brand share and which families carry non-brand traffic. Check which families existed before the inflection (Wayback) and which came after. |
| **AI answers** | The same prompt set for the target and the user in a connected prompt tracker (share of voice and citations over time), or by hand in ChatGPT, Perplexity, Gemini, and Google AI Overviews: the order brands are named and which pages are cited. Options in [references/data-sources.md](references/data-sources.md); method in **ai-seo**. |
| **Paid** | Per network, from `sc-ads` (ScrapeCreators) or the ad library links: active ads, the longest-running ad (45+ days suggests it's worth testing, though performance is unknown), the hook, the offer, and the landing page. No ads found → Observed "no ads found in [library, filter, date]", which is not proof they don't advertise. Read creative in depth with **ad-creative**. |
| **Social and creators** | The founders' and brand's best posts, the most-viewed creator videos and threads about the company, and every contest, giveaway, launch-week, or invite drop. Label each item team, creator (paid if disclosed), user, or press. |
| **PR and launches** | Funding announcements, launch days, press waves, and podcast runs, with dates. Founder-stated revenue is labeled "reported". |

### Activation

Read the sign-up and getting-started pages, docs, and demo videos: sign-up method (SSO, email, card up front, waitlist), what the first screen asks, and how quickly a new user reaches a first result. Stage 3 confirms this first-hand.

### Revenue

Plans, prices, what's free, and how the trial ends (days, credits, or usage). The `history` command shows when pricing changed. Line those months up with the curve, because packaging changes often move growth.

### Referral

Every place a non-user meets the product: shared outputs, public pages, embeds, invites, and "made with" or "powered by" badges. Confirm loops with backlink anchor data: one anchor text on thousands of domains is a loop, and its first-seen date dates it. Note any affiliate or referral program (the `stack` command catches Rewardful, PartnerStack, Tolt, FirstPromoter, and Dub) and its reward.

### Retention and Expansion Signals

These are lighter evidence, but they're dated: changelog cadence, lifecycle email and in-app messaging tools (from `stack`), and team or enterprise plans added over time.

### Hiring

`teardown.mjs jobs <domain>` reads a public Greenhouse, Lever, or Ashby board: open roles by function, the marketing channels being hired for, tools named in the posts, languages required, and first-marketing-hire signals. New roles show which channel is being scaled next. Stage 4 reads the full posts.
