---
name: landing-not-slop
description: Marketing pages that do not read as machine-generated. Use when writing or reviewing a landing, hero, pricing table, feature grid or marketing copy.
---

# Landings that do not read as generated

A page assembled from the defaults reads as generated within two seconds, and the
visitor's conclusion is not "this is AI" — it is "this is not a real company". That
judgement lands before a single word is read, which is why this is a design problem
rather than a copy problem.

The tells below are concrete because "make it look modern" is not actionable and
produces the same page every time.

## The visual tells, and what to do instead

| Tell | Why it reads as generated | Instead |
|---|---|---|
| Purple-to-blue (or teal-to-indigo) gradient hero | The single most-copied default of the 2023-2025 era | One flat brand colour, or a photograph, or a product screenshot on a plain field |
| Three feature cards, each an icon + 3-word title + 2 lines | The shape a model reaches for when it has nothing specific to say | However many features are real. Two is fine. Show the thing working |
| Generic line-art icons (rocket, lightning, shield, gear) | Chosen for "tech feel", carry zero information | Product screenshots, a number, or nothing |
| Everything centred, everything the same width | No hierarchy means nothing is important | Asymmetry. One thing on the page is biggest, and it is the one that matters |
| Glassmorphism, heavy blur, floating 3D blobs | Peaked in 2022 and now dates a page instantly | Flat surfaces, real borders, one shadow depth |
| Every section fading in on scroll | Motion applied uniformly is decoration, not communication | Motion on one element that rewards attention, or none |
| Emoji as section markers | 🚀 ✨ 💡 in headings is the strongest single tell | Words |
| Dark hero, light body, dark footer | Alternation for its own sake | One background. Change it only where the meaning changes |

## The copy tells

- **"Seamlessly", "effortlessly", "unlock", "elevate", "supercharge", "revolutionise",
  "game-changing", "cutting-edge", "robust", "leverage", "empower".** Any of these in a
  headline means the headline says nothing. Delete the word and see if the sentence
  survives; usually it improves.
- **Triads.** "Fast, simple, and secure." "Build, ship, and scale." The rule of three is
  the most obvious rhythm in the training data. Break it — two, or four, or one.
- **The em-dash-heavy voice** with "It's not just X — it's Y." That construction is a
  tell on its own now.
- **Headlines that describe the category, not the product.** "The modern way to manage
  your finances" describes a category. "See every subscription you forgot about" is a
  product.
- **Testimonials with full names, job titles, and companies that do not exist.** Never
  fabricate one. No testimonials beats invented testimonials — and invented ones are a
  legal exposure, not just a taste failure.
- **Stats with no source.** "10x faster" with nothing behind it. Either cite the
  measurement or cut the number.

## What a real page has that a generated one does not

1. **A specific first screen.** Name what the product does in the visitor's words, not
   the category's. If the headline would fit a competitor, it is not a headline.
2. **Evidence in view without scrolling.** A screenshot, a number, a named customer, a
   thirty-second video. Something checkable.
3. **One primary action.** Not "Get started" beside "Book a demo" beside "Read docs".
   The page has one job.
4. **Text set in something other than the framework default.** Inter and system-ui at
   default weights are the visual signature of an unstyled page. Pick a typeface and
   commit to a scale.
5. **Objection handling.** Price, what happens to the data, how to leave. Absence of
   these reads as evasion.

## Method

1. **Look at three real products in this space before writing anything.** You have web
   access; use it. Not to copy, but because "current practice" is a moving target and
   the training data is behind it.
2. **Write the copy first, in a text editor, with no layout.** If it is dull as plain
   text no layout rescues it.
3. **Build the specific screen the product needs**, then take away whatever survives
   removal.
4. **Read the tell tables above against what you built.** Every match is a decision to
   make deliberately or reverse.
5. **Check it at 375px wide.** Most traffic is there and most generated pages are
   designed at 1440.

## Accessibility, which is not separate from quality

Contrast at least 4.5:1 for body text. Focus rings visible and never `outline: none`.
Every image gets alt text describing its content, not its filename. Hit targets 44px.
Motion behind `prefers-reduced-motion`. A page that fails these fails real users, and
the failures correlate almost exactly with the generated look.
