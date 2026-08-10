/**
 * The landing page FAQ — one source for the rendered text and the `FAQPage`
 * JSON-LD.
 *
 * They must not drift. Google treats FAQ markup whose answers are not visible
 * on the page as a structured-data violation, and an LLM that quotes an answer
 * nobody can see on the page is quoting something we can't stand behind. Both
 * consumers read this array, so there is only one text to keep honest.
 *
 * Answers are written to be quotable on their own: each opens with the actual
 * answer rather than a wind-up, because a model lifting one sentence will lift
 * the first one.
 */
export interface FaqItem {
  q: string
  a: string
}

export const FAQ: FaqItem[] = [
  {
    q: "What speed should I drive an EV on a long trip?",
    a: "On most European motorway trips the quickest cruise speed for a modern EV lands between 110 and 130 km/h. Below that you are simply driving for longer; above it, consumption rises with the square of speed, so each extra 10 km/h buys back less time on the road than it adds at the charger. The exact figure depends on your car's charging curve and how far apart the fast chargers on your route are, which is what this app simulates.",
  },
  {
    q: "Does driving slower in an EV actually get you there sooner?",
    a: "Sometimes, but not as often as people assume. Slowing down helps when it removes a charging stop entirely, or when it lets you arrive at a stop low enough to use the fast part of your car's charging curve. If it does neither, you have just added road time for nothing. The saving is a step function, not a smooth curve, which is why the total-time-vs-speed chart on this site is usually flat with one or two cliffs rather than a straight line.",
  },
  {
    q: "How much does speed reduce EV range?",
    a: "Aerodynamic drag rises with the square of speed, so consumption follows a curve of roughly Wh/km = a + b·v². In practice a typical family EV uses about 20 to 30 percent more energy per kilometre at 130 km/h than at 100 km/h. Each car on this site lists its own fitted coefficients and the resulting consumption at every cruise speed from 90 to 160 km/h.",
  },
  {
    q: "Is it faster to charge to 80% or to 100%?",
    a: "Almost always 80% or less. DC charging power tapers steeply as the battery fills, so the last 20% can take as long as the first 60%. The optimal plan is usually to arrive at a charger fairly low and leave before the taper bites — often somewhere around 60 to 80%. This app's planner is not told that rule; it searches every possible stop plan and the pattern falls out on its own.",
  },
  {
    q: "How many charging stops will I need?",
    a: "That depends on your usable battery, your cruise speed, the fast chargers that actually exist along your route, and the state of charge you want on arrival. The planner searches real charger locations from OpenChargeMap against your car's charging curve and returns the stop plan with the lowest total journey time, so the number of stops is an output rather than something you pick.",
  },
  {
    q: "Does cold weather change the best cruise speed?",
    a: "Yes, and usually downward. Cold raises consumption and, more importantly, cuts how much power the battery will accept, which makes every charging stop longer. When stops get more expensive, the balance shifts toward driving slower and stopping less. You can set an ambient temperature on the planner and watch the optimum move.",
  },
  {
    q: "Is this a route planner like ABRP?",
    a: "It answers a narrower question. Most EV route planners assume a speed and then plan your charging around it. This one sweeps every cruise speed from 90 to 160 km/h, plans the optimal charging stops separately for each, and shows you the resulting total journey time for all of them — so you can see what your speed choice is actually costing or saving before you set off.",
  },
]
