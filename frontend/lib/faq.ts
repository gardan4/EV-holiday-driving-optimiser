/**
 * The landing page FAQ. One source for the rendered text and the `FAQPage`
 * JSON-LD.
 *
 * They must not drift. Google treats FAQ markup whose answers are not visible
 * on the page as a structured-data violation, and an LLM that quotes an answer
 * nobody can see on the page is quoting something we can't stand behind. Both
 * consumers read this array, so there is only one text to keep honest.
 *
 * Answers open with the actual answer rather than a wind-up, because a model
 * lifting one sentence will lift the first one.
 *
 * Where an answer cites a number it comes from a real run on this site, named
 * in the answer so anyone can reproduce it. The first answer used to say the
 * optimum "lands between 110 and 130". A real Utrecht to Innsbruck plan puts it
 * at 145, so that was simply wrong, and wrong on the one page built to be
 * quoted. Don't put a figure in here you haven't planned.
 */
export interface FaqItem {
  q: string
  a: string
}

export const FAQ: FaqItem[] = [
  {
    q: "What speed should I drive an EV on a long trip?",
    a: "Faster than most advice suggests, and above roughly 130 km/h it stops making much difference. On a real 956 km run from Utrecht to Innsbruck in a Cupra Born 58, driving 130 took 10h41 and the quickest speed of all, 145, took 10h36. That is five minutes for fifteen more km/h. Drop to 100 and the same trip takes 11h40. The useful rule is not to crawl, because the penalty for going slow is much bigger than the prize for going fast.",
  },
  {
    q: "Does driving slower in an EV actually get you there sooner?",
    a: "Usually not. Slowing down does remove charging stops, and on the Utrecht to Innsbruck run above it takes you from eight stops down to five. The problem is that those stops are worth about fifteen minutes each and the extra time on the road is worth an hour. You end up trading cheap minutes for expensive ones. Slowing down wins only in the narrow case where it removes a stop without adding much road time, which tends to happen when the chargers on your route are awkwardly spaced.",
  },
  {
    q: "How much does speed reduce EV range?",
    a: "Aerodynamic drag rises with the square of speed, so consumption follows a curve of roughly Wh/km = a + b·v². A typical family EV uses about 20 to 30 percent more energy per kilometre at 130 km/h than at 100. For a Cupra Born 58 that is 160 Wh/km at 100 and 232 Wh/km at 130, which turns 363 km of range into 250. Every car on this site lists its own coefficients and what they work out to at each cruise speed.",
  },
  {
    q: "Is it faster to charge to 80% or to 100%?",
    a: "Almost always 80% or less. Charging power tapers steeply as the battery fills, and on a Cupra Born 58 the last 20% takes about as long as the whole 10 to 80% stretch. The quickest plans usually arrive at a charger fairly low and leave somewhere around 60 to 80%, before the taper bites. Nothing in this app's planner is told that rule. It searches every possible stop plan and the pattern turns up on its own.",
  },
  {
    q: "How many charging stops will I need?",
    a: "It depends on your battery, your speed, and where the fast chargers actually are on your route. The number is an output rather than something you choose. Utrecht to Innsbruck in a Born 58 needs five stops at 100 km/h and eight at 130, because going faster means arriving at each charger sooner and lower. A bigger battery moves those numbers a lot, which is why the planner asks which car you drive before it answers.",
  },
  {
    q: "Does cold weather change the best cruise speed?",
    a: "Yes, and it pushes it downward. Cold raises consumption, and more importantly it cuts how much power the battery will accept, so every stop takes longer. Once stops get expensive the balance tips towards driving slower and stopping less often. You can set an ambient temperature on the planner and watch the optimum move.",
  },
  {
    q: "Is this a route planner like ABRP?",
    a: "It answers a narrower question. Most EV route planners take a speed as given and plan your charging around it. This one sweeps every cruise speed from 90 to 160 km/h, works out the best charging plan separately for each, and shows the total journey time for all of them side by side. So it is less useful for navigation and more useful for deciding how hard to drive before you set off.",
  },
]
