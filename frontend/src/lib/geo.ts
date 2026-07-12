/** Haversine distance in km between two WGS84 points. */
export function haversineKm(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export interface MapLinks {
  amap: string;
  baidu: string;
  google: string;
  apple: string;
}

/** Deep links that open hospital search in external map apps (no SDK key). */
export function buildMapLinks(hospitalName: string, city: string): MapLinks {
  const q = [hospitalName, city].filter(Boolean).join(" ");
  const enc = encodeURIComponent(q);
  return {
    amap: `https://uri.amap.com/search?keyword=${enc}`,
    baidu: `https://api.map.baidu.com/place/search?query=${enc}&region=${encodeURIComponent(city || "全国")}&output=html&src=webapp.pekinghsc.rd`,
    google: `https://www.google.com/maps/search/?api=1&query=${enc}`,
    apple: `https://maps.apple.com/?q=${enc}`,
  };
}
