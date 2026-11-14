# Stage 1: Build the Spine

The spine is two files every later stage writes into:

- **`traffic.csv`** (`month,visits,brand_searches`): the monthly curve from **one** source for the whole window. Mixing vendors creates jumps that never happened. Add brand search volume (or Google Trends interest) as a second demand signal that doesn't depend on SEO.
- **`moves.csv`** (`date,channel,kind,move,reach,link,evidence`): one row per dated move. Every stage adds rows.

Run the free collectors first. `scripts/` is inside this skill's directory: call the script by its full path from the user's project (`node <skill-dir>/scripts/teardown.mjs ...`) so outputs land in the project.

```bash
node scripts/teardown.mjs site target.com --out teardowns/target/urls.csv   # page families from the sitemap
node scripts/teardown.mjs stack target.com --months 24                       # first month each pixel or script appears in Wayback
node scripts/teardown.mjs history target.com/pricing --months 24             # months the pricing changed
node scripts/teardown.mjs ads "Target" --domain target.com                   # ad library links per network
```

Then identify the industry and load its pack:

```bash
node scripts/teardown.mjs detect target.com                 # likely industry packs, with the reasons
```

Confirm the industry with the user, then read the matching pack below. Each pack adds the signals, sources, first-hand checks, copying limits, plays, and metrics that matter in that industry. A company can need two packs (a marketplace for local services: load both).

| Industry | Pack |
|---|---|
| SaaS (PLG, sales-led, dev tools, AI apps) | [industries/saas.md](references/industries/saas.md) |
| Ecommerce and DTC | [industries/ecommerce-dtc.md](references/industries/ecommerce-dtc.md) |
| Consumer hardware and crowdfunding | [industries/consumer-hardware.md](references/industries/consumer-hardware.md) |
| CPG, food, and beverage | [industries/cpg-food-beverage.md](references/industries/cpg-food-beverage.md) |
| Retail and omnichannel | [industries/retail.md](references/industries/retail.md) |
| Franchising (unit growth and franchise sales) | [industries/franchising.md](references/industries/franchising.md) |
| Local and home services | [industries/local-services.md](references/industries/local-services.md) |
| Residential solar and home energy | [industries/solar-home-energy.md](references/industries/solar-home-energy.md) |
| Healthcare | [industries/healthcare.md](references/industries/healthcare.md) |
| Care services (senior care, home care, childcare) | [industries/care-services.md](references/industries/care-services.md) |
| Fitness and wellness | [industries/fitness-wellness.md](references/industries/fitness-wellness.md) |
| Legal and professional services | [industries/legal-professional-services.md](references/industries/legal-professional-services.md) |
| Real estate | [industries/real-estate.md](references/industries/real-estate.md) |
| Finance (banking, lending, cards, wealth) | [industries/finance.md](references/industries/finance.md) |
| Insurance | [industries/insurance.md](references/industries/insurance.md) |
| Crypto and web3 | [industries/crypto-web3.md](references/industries/crypto-web3.md) |
| Gambling and betting | [industries/gambling-betting.md](references/industries/gambling-betting.md) |
| Regulated goods (alcohol, cannabis, nicotine, firearms) | [industries/regulated-goods.md](references/industries/regulated-goods.md) |
| Manufacturing and industrial | [industries/manufacturing-industrial.md](references/industries/manufacturing-industrial.md) |
| Logistics and transportation | [industries/logistics-transportation.md](references/industries/logistics-transportation.md) |
| Automotive | [industries/automotive.md](references/industries/automotive.md) |
| Govtech and public sector | [industries/govtech-public-sector.md](references/industries/govtech-public-sector.md) |
| Agencies and B2B services | [industries/agencies-b2b-services.md](references/industries/agencies-b2b-services.md) |
| Nonprofit | [industries/nonprofit.md](references/industries/nonprofit.md) |
| Marketplaces | [industries/marketplaces.md](references/industries/marketplaces.md) |
| Mobile apps and mobile games | [industries/mobile-apps-games.md](references/industries/mobile-apps-games.md) |
| PC and console games | [industries/games-pc-console.md](references/industries/games-pc-console.md) |
| Education | [industries/education.md](references/industries/education.md) |
| Hospitality and travel | [industries/hospitality-travel.md](references/industries/hospitality-travel.md) |
| Events and entertainment | [industries/events-entertainment.md](references/industries/events-entertainment.md) |
| Media and creators | [industries/media-creators.md](references/industries/media-creators.md) |

Industry commands: `shopify` (public catalog with dates, bestseller order, apps), `phones` (call-tracking check), `filings` (SEC filings and Form D raises), `cfpb` (consumer complaints by month), `fdic` (bank quarterly financials), and `adoption` (npm and PyPI downloads and GitHub stars by month). The packs say when to run each. For developer tools, monthly downloads from one registry can serve as the traffic curve.

Each ad pixel's first month becomes a move (`kind: ad-start`, evidence: Inferred, since a pixel shows the channel was possible, not that ads ran). Each pricing change becomes a move. Then mark the **inflection**: the month where growth stepped up and stayed up for at least two months. There can be more than one.
