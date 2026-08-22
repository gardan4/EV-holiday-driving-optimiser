"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getVehicles, planTrip, PlacePoint, Vehicle } from "@/lib/client"
import { track } from "@/lib/analytics"
import { defaultDepartureIso } from "@/lib/format"
import { describeTemp } from "@/lib/weather"
import { freeflowFactorFor } from "@/lib/driving"
import { describePayload } from "@/lib/payload"
import {
  InfoTip,
  listFieldNames,
  NumberField,
  revealField,
  useNumberFieldValidity,
} from "./fields"
import DepartureField from "./DepartureField"
import GeocodeInput from "./GeocodeInput"
import VehiclePicker from "./VehiclePicker"
import { useTrips } from "../trips/TripsContext"


/** The planner form. On submit: POST /api/trips → navigate to the permalink. */
export default function TripForm() {
  const router = useRouter()
  const { noteTripPlanned } = useTrips()
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [origin, setOrigin] = useState<PlacePoint | null>(null)
  const [dest, setDest] = useState<PlacePoint | null>(null)
  const [vehicleId, setVehicleId] = useState<string | null>(null)
  const [departure, setDeparture] = useState(defaultDepartureIso())
  const [departSoc, setDepartSoc] = useState(100)
  const [targetSoc, setTargetSoc] = useState(10)
  const [tempC, setTempC] = useState(20)
  const [winterTyres, setWinterTyres] = useState(false)
  const [overCap, setOverCap] = useState(15)
  const [motorwayCap, setMotorwayCap] = useState(130)
  const [noLimits, setNoLimits] = useState(false)
  const [autobahnShare, setAutobahnShare] = useState(50)
  const [stopOverhead, setStopOverhead] = useState(5)
  const [sitePower, setSitePower] = useState(80)
  const [queue, setQueue] = useState(5)
  const [restEveryH, setRestEveryH] = useState(3)
  const [restMin, setRestMin] = useState(20)
  const [price, setPrice] = useState(0.59)
  const [occupants, setOccupants] = useState(2)
  const [luggageKg, setLuggageKg] = useState(30)
  const [submitting, setSubmitting] = useState(false)
  // A half-typed or emptied box must not be silently replaced by a default —
  // it blocks the submit and shows red until it holds a real number.
  const { allValid, badFields, onValidity } = useNumberFieldValidity()

  useEffect(() => {
    getVehicles()
      .then((vs) => {
        setVehicles(vs)
        if (vs.length > 0) setVehicleId((prev) => prev ?? vs[0].id)
      })
      .catch(() => toast.error("Could not load the car list. Is the API running?"))
  }, [])

  const ready = origin && dest && vehicleId && allValid && !submitting

  // Why the button is off. A disabled primary action that says nothing is
  // indistinguishable from a broken one — and every number here ships with a
  // working default, so the reader arriving at a grey button has either not
  // named the two places yet or has emptied a box somewhere. Both are worth a
  // sentence, in the order they have to be fixed.
  const blocker: { text: string; wrong: boolean } | null = submitting
    ? null
    : badFields.length > 0
      ? {
          text: `${listFieldNames(badFields)} ${badFields.length > 1 ? "need" : "needs"} a usable number — take me there`,
          wrong: true,
        }
      : !vehicleId
        ? { text: "Pick the car you're driving.", wrong: false }
        : !origin || !dest
          ? {
              text: !origin && !dest
                ? "Choose where you're driving from and to."
                : `Choose where you're driving ${origin ? "to" : "from"}.`,
              wrong: false,
            }
          : null

  // Priced against this car at a typical 130 km/h cruise, so the figure moves
  // when you switch cars — a 58 kWh Born loses more range per kilo than a big
  // battery does.
  const car = vehicles.find((v) => v.id === vehicleId)
  const payloadReadout = car
    ? describePayload(
        occupants,
        luggageKg,
        car.usable_kwh,
        car.consumption.a_wh_km + car.consumption.b_wh_km_per_kph2 * 130 * 130,
      )
    : undefined

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!origin || !dest || !vehicleId) return
    setSubmitting(true)
    // Counted before the await, so a plan that never returns still shows up as
    // an attempt — the gap between submitted and planned is the interesting
    // number, and only counting successes would hide it.
    track("plan_submitted")
    try {
      const trip = await planTrip({
        origin,
        dest,
        vehicle_id: vehicleId,
        departure_iso: departure,
        depart_soc: departSoc,
        target_soc: targetSoc,
        temperature_c: tempC,
        winter_tyres: winterTyres,
        autobahn_open_share: autobahnShare / 100,
        motorway_cap_kph: motorwayCap,
        ignore_speed_limits: noLimits,
        over_cap_kph: overCap,
        over_freeflow_factor: freeflowFactorFor(overCap),
        stop_overhead_min: stopOverhead,
        site_power_factor: sitePower / 100,
        queue_min: queue,
        rest_interval_min: restEveryH > 0 ? restEveryH * 60 : 0,
        rest_min: restEveryH > 0 ? restMin : 0,
        price_per_kwh: price,
        occupants,
        luggage_kg: luggageKg,
      })
      track("trip_planned")
      // The badge on the header button, kept honest without a fetch. Under a
      // username the server has just stamped this trip onto the list, so the
      // count is one higher whether or not anybody opens the panel to see it.
      noteTripPlanned()
      router.push(`/trip/${trip.id}`)
    } catch (err) {
      // Deliberately not counted here. The server records `plan_failed` with
      // the reason it actually failed for — which this side cannot know, and
      // which an ad blocker cannot suppress. Counting it in both places would
      // double every failure and make the funnel wrong.
      //
      // A request that never reaches the server is therefore not in the
      // failure count. It shows up as the gap between `plan_submitted` and
      // `trip_planned + plan_failed`, which is where it belongs: nothing
      // server-side happened, so nothing server-side can explain it.
      toast.error(err instanceof Error ? err.message : "Planning failed")
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-ink-100 bg-white/90 p-5 shadow-xl shadow-ink-900/5 backdrop-blur sm:p-6"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <GeocodeInput label="From" placeholder="Utrecht…" value={origin} onChange={setOrigin} />
        <GeocodeInput label="To" placeholder="Innsbruck…" value={dest} onChange={setDest} />
      </div>

      <div className="mt-4">
        <VehiclePicker vehicles={vehicles} value={vehicleId} onChange={setVehicleId} />
      </div>

      <FieldGroup title="When you leave" hint="weather moves the best speed more than anything else here">
        <div className="space-y-4">
        <DepartureField value={departure} onChange={setDeparture} />
        {/* Full width, not half: it is the only field in the row, and a box
            ending at the middle of the card just leaves a hole beside it. */}
        <NumberField
          id="temp"
          onValidity={onValidity}
          label="Temperature"
          unit="°C"
          value={tempC}
          min={-30}
          max={45}
          step={1}
          onChange={setTempC}
          explain="The temperature you expect for most of the drive. Cold hits twice: the car uses more energy AND the battery accepts charge more slowly. The second effect is the bigger one. It can move the best cruise speed down by 30 km/h."
          readout={describeTemp(tempC)}
        />
        </div>
        {/* Deliberately its own control rather than something inferred from the
            temperature above: tyres are fitted for a season, so a mild wet day
            on winter rubber still pays for them, and somebody driving through
            an Alpine February on summer tyres should not be charged for grip
            they do not have. */}
        <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-xl bg-ink-100 px-3.5 py-3 transition-colors hover:bg-ink-200">
          <input
            type="checkbox"
            checked={winterTyres}
            onChange={(e) => setWinterTyres(e.target.checked)}
            className="h-5 w-5 shrink-0 accent-brand-500"
          />
          <span className="flex min-w-0 items-center gap-1.5 text-sm font-semibold text-ink-900">
            On winter tyres
            <InfoTip>
              Softer compound and deeper tread cost roughly 5-10% in energy, and
              more the slower you go, because it is a cost per kilometre rather
              than a fight with the air. A heavier car pays more of it.
            </InfoTip>
          </span>
        </label>
      </FieldGroup>

      <FieldGroup title="What you’re carrying" hint="weight costs energy on every climb">
        <div className="grid gap-4 sm:grid-cols-2">
        <NumberField
          id="occupants"
          onValidity={onValidity}
          label="People aboard"
          unit="people"
          value={occupants}
          min={1}
          max={9}
          step={1}
          onChange={setOccupants}
          explain="Everyone in the car, driver included, counted at 75 kg each. Weight costs energy through rolling resistance on the flat and through sheer lifting on a climb. A full car on an alpine run is a real handicap."
        />
        <NumberField
          id="luggage"
          onValidity={onValidity}
          label="Luggage"
          unit="kg"
          value={luggageKg}
          min={0}
          max={400}
          step={5}
          onChange={setLuggageKg}
          explain="Suitcases, ski gear, the roof box's contents. Note the model prices the WEIGHT only: a roof box or bike rack also wrecks the aerodynamics, which at motorway speed usually costs far more than whatever is inside it."
          readout={payloadReadout}
        />
        </div>
      </FieldGroup>

      <FieldGroup title="Battery" hint="how full you set off, and what you want left on arrival">
        <div className="grid gap-4 sm:grid-cols-2">
        <SocSlider
          id="depart-soc"
          label="Battery at departure"
          explain="How full the car is when you set off. Charge at home the night before and this is 100%."
          value={departSoc}
          min={30}
          max={100}
          onChange={setDepartSoc}
        />
        <SocSlider
          id="target-soc"
          label="Battery on arrival"
          explain="The least you want left when you get there. Leave enough to reach the chalet, the shops and a charger. Low means you arrive nearly empty, which is the fastest plan."
          value={targetSoc}
          min={5}
          max={80}
          onChange={setTargetSoc}
        />
        </div>
      </FieldGroup>

      {/* Promoted out of the accordion. These decide what speed is even
          legal, so the answer is built on them — and outside western
          Europe the default is a guess the reader has to correct. Behind a
          closed "fine-tune" panel, nobody corrected it. */}
      {/* Carries the assumptions line that used to sit in grey under the whole
          form. Every claim in it is a claim about these three fields, so it
          belongs on them rather than floating beneath the card. */}
      <FieldGroup
        title="How you drive"
        hint="The answer is built on these. The plan assumes motorway cruise at your chosen speed wherever that is legal — the real limits in AT/NL and the rest of western Europe, derestricted where the German autobahn allows — plus the real fast chargers along your route and your car's measured charging curve."
      >
        <div className="space-y-4">
        {/* The whole point of the tool is that fast driving isn't free, so
            the honest way to argue with a speed limit is to let people see
            what removing it actually buys — usually another charging stop. */}
        <label className="flex cursor-pointer items-center gap-3 rounded-xl bg-ink-100 px-3.5 py-3 transition-colors hover:bg-ink-200">
          <input
            type="checkbox"
            checked={noLimits}
            onChange={(e) => setNoLimits(e.target.checked)}
            className="h-5 w-5 shrink-0 accent-brand-500"
          />
          <span className="flex min-w-0 items-center gap-1.5 text-sm font-semibold text-ink-900">
            Ignore speed limits entirely
            <InfoTip>
              Every motorway treated as derestricted, so your cruise speed is the only
              limit, so what the drive would look like if the law weren&apos;t there.
              Ordinary roads still go at their own pace.
            </InfoTip>
          </span>
        </label>

        <div className={`grid gap-4 sm:grid-cols-2 ${noLimits ? "sm:grid-cols-1" : ""}`}>
        {/* Unmounted, not hidden. A `hidden` box still holds whatever was
            typed into it, so half-clearing the limit and then ticking "ignore
            speed limits" left the form permanently unsubmittable, pointing at
            a red field that was not on the screen. Unmounting hands its
            validity flag back (see NumberField's cleanup); `motorwayCap`
            itself lives up here, so the value survives unticking. */}
        {!noLimits && (
        <div>
        <NumberField
          id="motorway-cap"
          onValidity={onValidity}
          label="Motorway limit"
          unit="km/h"
          value={motorwayCap}
          min={80}
          max={200}
          step={5}
          onChange={setMotorwayCap}
          explain="The legal motorway limit where you're driving. Germany, the Netherlands, Belgium, Luxembourg, France, Switzerland, Austria and Italy use their own real limits and ignore this. Everywhere else uses it, because those are the only countries whose limits are modelled. 113 is 70 mph, 121 is 75."
          readout={
            motorwayCap === 130
              ? "130 is a western-European motorway, so lower it elsewhere"
              : `${motorwayCap} km/h ≈ ${Math.round(motorwayCap / 1.609)} mph`
          }
        />
        </div>
        )}

        <NumberField
          id="over-cap"
          onValidity={onValidity}
          label="Over the limit"
          unit="km/h"
          value={overCap}
          min={0}
          max={30}
          step={1}
          onChange={setOverCap}
          explain="How far above the posted limit you actually sit. Saves driving time but burns more energy, so past a point it simply buys you another charging stop. Ordinary roads lift more gently than the autobahn does."
          readout={
            overCap === 0
              ? "sitting on the limit"
              : `+${overCap} on limited roads, about +${Math.round((freeflowFactorFor(overCap) - 1) * 100)}% elsewhere`
          }
        />
        </div>
        {!noLimits && (
        <div>
          <label
            htmlFor="autobahn"
            className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-ink-500"
          >
            <span className="flex items-center gap-1.5">
              German autobahn actually open
              <InfoTip>
                Germany only, so it does nothing on a route that never enters it. How much
                of the derestricted-looking autobahn you&apos;ll really get to use. The
                rest is roadworks, traffic and signposted limits. About 30% is realistic.
                Set it to 100% and fast driving looks free, which is the assumption that
                flatters high speeds most.
              </InfoTip>
            </span>
            <span className="rounded-md bg-brand-50 px-1.5 py-0.5 font-mono text-xs font-bold text-brand-700">
              {autobahnShare}%
            </span>
          </label>
          <input
            id="autobahn"
            type="range"
            min={0}
            max={100}
            step={5}
            value={autobahnShare}
            onChange={(e) => setAutobahnShare(Number(e.target.value))}
            className="h-6 w-full accent-brand-500"
          />
        </div>
        )}
        </div>
      </FieldGroup>

      <details className="group mt-4 rounded-xl border border-ink-200 bg-white px-4 py-3">
        <summary className="flex cursor-pointer select-none items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500 marker:content-['']">
          Charging, breaks and the fine print
          <InfoTip>
            Sensible defaults for a European fast-charging network — open it if yours
            differ. What you pay per kWh, how long each stop really costs you around
            the charging itself, how much of a charger&apos;s rating reaches your car,
            and when you stop to rest.
          </InfoTip>
        </summary>

        <div className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <NumberField
              id="stop-overhead"
              onValidity={onValidity}
              label="Time per stop"
              unit="min"
              value={stopOverhead}
              min={0}
              max={30}
              onChange={setStopOverhead}
              explain="Everything around the charging itself: leaving the motorway, parking, plugging in, paying, getting back on. Count about 5 minutes if you plug and go, 12 if there's a detour and payment faff. Higher values punish plans with many short stops."
            />
            <NumberField
              id="site-power"
              onValidity={onValidity}
              label="Charger power"
              unit="%"
              value={sitePower}
              min={30}
              max={100}
              step={5}
              onChange={setSitePower}
              explain="A charger's rating is the headline figure, not what reaches your car. A 350 kW cabinet shared with the car beside you, or a busy Supercharger splitting a stall pair, delivers roughly half. This is throughput while you're plugged in. The queue field below covers waiting for a plug."
              readout={
                sitePower >= 100
                  ? "every charger delivers its full rating"
                  : `a 350 kW site behaves like ${Math.round(350 * (sitePower / 100))} kW`
              }
            />
            <NumberField
              id="queue"
              onValidity={onValidity}
              label="Queue"
              unit="min"
              value={queue}
              min={0}
              max={30}
              onChange={setQueue}
              explain="Waiting for a free stall. Nil in the middle of the night; 10 or more on a holiday getaway weekend. The more stops a plan makes, the more times you risk it, which is the honest cost of driving fast."
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="grid grid-cols-2 gap-4 sm:col-span-2">
              <NumberField
                id="rest-every"
                onValidity={onValidity}
                label="Break every"
                unit="h"
                value={restEveryH}
                min={0}
                max={8}
                onChange={setRestEveryH}
                explain="How long you'll drive before stopping to rest. Time already spent standing at a charger counts towards it, so a fast plan with many stops usually gets its breaks for free. A slow plan has to add them. Set 0 if you'd rather not model breaks."
                readout={restEveryH === 0 ? "no breaks modelled" : undefined}
              />
              <NumberField
                id="rest-min"
                onValidity={onValidity}
                label="Break length"
                unit="min"
                value={restMin}
                min={0}
                max={120}
                step={5}
                onChange={setRestMin}
                explain="How long each of those breaks lasts."
              />
            </div>
            <NumberField
              id="price"
              onValidity={onValidity}
              label="Charging price"
              unit="€/kWh"
              value={price}
              min={0}
              max={3}
              step={0.01}
              onChange={setPrice}
              explain="What you pay at a public fast charger, about €0.59 on most European networks without a subscription. Priced at the plug, so it already includes charging losses."
            />
          </div>
        </div>
      </details>

      <button
        type="submit"
        disabled={!ready}
        // The press is answered on the way down. This button submits a form and
        // then navigates, so its hover state is gone before anything visible
        // happens — without `:active` the only feedback for the app's primary
        // action is the page eventually changing. 0.98 rather than 0.97: it is
        // full-width, and the same ratio reads as a bigger movement the wider
        // the element gets.
        className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-ink-900 px-6 py-3.5 font-display text-base font-semibold text-white transition-[transform,background-color,color] duration-150 ease-out hover:bg-ink-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-ink-200 disabled:text-ink-400 disabled:active:scale-100"
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Simulating every speed…
          </>
        ) : (
          <>
            Find my fastest speed
            <ArrowRight className="h-4 w-4" />
          </>
        )}
      </button>
      {/* The live region itself is always mounted — one that appears at the
          same moment as its text is not reliably announced. Empty, it
          collapses to nothing. */}
      <p aria-live="polite" className="text-center text-xs empty:hidden [&:not(:empty)]:mt-2">
        {blocker &&
          (blocker.wrong ? (
            // Clickable, because the reader may have folded the fine print back
            // up over the box that is blocking them — at which point a message
            // naming a field they cannot see is only half an answer.
            <button
              type="button"
              onClick={() => revealField(badFields[0].id)}
              className="text-red-600 underline decoration-red-300 underline-offset-2 hover:decoration-red-600"
            >
              {blocker.text}
            </button>
          ) : (
            <span className="text-ink-500">{blocker.text}</span>
          ))}
      </p>
    </form>
  )
}

/** A titled group of fields. The form was a flat run of nine uppercase labels
 *  with no hierarchy, which is what pushed everything interesting into an
 *  accordion in the first place — headings buy back the room to leave it out.
 *
 *  The hint is carried by the heading's tip rather than printed beside it: four
 *  of them, one per section, is four grey sentences the reader scrolls past on
 *  the way to the fields. */
function FieldGroup({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="mt-5 border-t border-ink-100 pt-4">
      <h3 className="mb-2.5 flex items-center gap-1.5 font-display text-sm font-semibold text-ink-900">
        {title}
        {hint && <InfoTip>{hint}</InfoTip>}
      </h3>
      {children}
    </section>
  )
}

function SocSlider({
  id,
  label,
  explain,
  value,
  min,
  max,
  onChange,
}: {
  id: string
  label: string
  explain?: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-ink-500"
      >
        <span className="flex items-center gap-1.5">
          {label}
          {explain && <InfoTip>{explain}</InfoTip>}
        </span>
        <span className="rounded-md bg-brand-50 px-1.5 py-0.5 font-mono text-xs font-bold text-brand-700">
          {value}%
        </span>
      </label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-6 w-full accent-brand-500"
      />
    </div>
  )
}

/** Hover/focus explainer. A real <button> so it also works from the keyboard
 * and on touch, where a title attribute shows nothing. */


