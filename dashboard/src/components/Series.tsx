import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import type { Point } from "../lib/api"
import { compact, shortDate } from "../lib/format"

const DRIVE = "#5b8ad4"
const MINT = "#43c389"

/** Page views and visitors on one shared axis.
 *
 * One axis, deliberately. Two y-axes let any pair of series be made to look
 * correlated by choosing the scales, and visitors are always a subset of page
 * views, so the honest picture is one of them sitting under the other. */
export function Series({
  views,
  visitors,
  granularity,
}: {
  views: Point[]
  visitors: Point[]
  granularity: string
}) {
  const byBucket = new Map(visitors.map((p) => [p.bucket, p.value]))
  const data = views.map((p) => ({
    bucket: p.bucket,
    views: p.value,
    visitors: byBucket.get(p.bucket) ?? 0,
  }))

  // At 90 daily buckets an every-tick axis is a smear; show roughly six.
  const stride = Math.max(1, Math.ceil(data.length / 6))
  const ticks = data.filter((_, i) => i % stride === 0).map((d) => d.bucket)

  return (
    <div className="h-[240px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id="viewsWash" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={DRIVE} stopOpacity={0.28} />
              <stop offset="100%" stopColor={DRIVE} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#26344a" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="bucket"
            ticks={ticks}
            tickFormatter={shortDate}
            tick={{ fill: "#7d8ca0", fontSize: 11 }}
            axisLine={{ stroke: "#26344a" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#7d8ca0", fontSize: 11 }}
            tickFormatter={compact}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: "#141d2c",
              border: "1px solid #26344a",
              borderRadius: 10,
              fontSize: 12,
            }}
            labelStyle={{ color: "#aab6c5" }}
            labelFormatter={(b) => shortDate(String(b))}
          />
          <Area
            type="monotone"
            dataKey="views"
            stroke={DRIVE}
            strokeWidth={2}
            fill="url(#viewsWash)"
            name={granularity === "hour" ? "page views / hour" : "page views"}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="visitors"
            stroke={MINT}
            strokeWidth={2}
            dot={false}
            name="visitors"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
