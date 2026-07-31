/** Polyline5 decoder (Google encoded polyline, precision 5) — mirrors
 * src/app/services/geo.py polyline_encode. */
export function decodePolyline(s: string): [number, number][] {
  const coords: [number, number][] = []
  let lat = 0
  let lon = 0
  let i = 0
  while (i < s.length) {
    for (const isLon of [false, true]) {
      let shift = 0
      let result = 0
      let b: number
      do {
        b = s.charCodeAt(i++) - 63
        result |= (b & 0x1f) << shift
        shift += 5
      } while (b >= 0x20)
      const d = result & 1 ? ~(result >> 1) : result >> 1
      if (isLon) lon += d
      else lat += d
    }
    coords.push([lat / 1e5, lon / 1e5])
  }
  return coords
}
