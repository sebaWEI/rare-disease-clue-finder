/** Map English / short city labels onto china.json province feature names. */
const CITY_TO_PROVINCE: Record<string, string> = {
  北京市: "北京市",
  Beijing: "北京市",
  天津市: "天津市",
  Tianjin: "天津市",
  河北省: "河北省",
  Hebei: "河北省",
  山西省: "山西省",
  Shanxi: "山西省",
  内蒙古自治区: "内蒙古自治区",
  内蒙古: "内蒙古自治区",
  "Inner Mongolia": "内蒙古自治区",
  辽宁省: "辽宁省",
  Liaoning: "辽宁省",
  吉林省: "吉林省",
  Jilin: "吉林省",
  黑龙江省: "黑龙江省",
  Heilongjiang: "黑龙江省",
  上海市: "上海市",
  Shanghai: "上海市",
  江苏省: "江苏省",
  Jiangsu: "江苏省",
  浙江省: "浙江省",
  Zhejiang: "浙江省",
  安徽省: "安徽省",
  Anhui: "安徽省",
  福建省: "福建省",
  Fujian: "福建省",
  江西省: "江西省",
  Jiangxi: "江西省",
  山东省: "山东省",
  Shandong: "山东省",
  河南省: "河南省",
  Henan: "河南省",
  湖北省: "湖北省",
  Hubei: "湖北省",
  湖南省: "湖南省",
  Hunan: "湖南省",
  广东省: "广东省",
  Guangdong: "广东省",
  广西壮族自治区: "广西壮族自治区",
  广西: "广西壮族自治区",
  Guangxi: "广西壮族自治区",
  海南省: "海南省",
  Hainan: "海南省",
  重庆市: "重庆市",
  Chongqing: "重庆市",
  四川省: "四川省",
  Sichuan: "四川省",
  贵州省: "贵州省",
  Guizhou: "贵州省",
  云南省: "云南省",
  Yunnan: "云南省",
  西藏自治区: "西藏自治区",
  西藏: "西藏自治区",
  Tibet: "西藏自治区",
  陕西省: "陕西省",
  Shaanxi: "陕西省",
  甘肃省: "甘肃省",
  Gansu: "甘肃省",
  青海省: "青海省",
  Qinghai: "青海省",
  宁夏回族自治区: "宁夏回族自治区",
  宁夏: "宁夏回族自治区",
  Ningxia: "宁夏回族自治区",
  新疆维吾尔自治区: "新疆维吾尔自治区",
  新疆: "新疆维吾尔自治区",
  Xinjiang: "新疆维吾尔自治区",
  香港特别行政区: "香港特别行政区",
  "Hong Kong": "香港特别行政区",
  澳门特别行政区: "澳门特别行政区",
  Macao: "澳门特别行政区",
  Macau: "澳门特别行政区",
  台湾省: "台湾省",
  Taiwan: "台湾省",
};

export function cityToProvince(city: string): string | null {
  const raw = city.trim();
  if (!raw) return null;
  if (CITY_TO_PROVINCE[raw]) return CITY_TO_PROVINCE[raw];
  const lower = raw.toLowerCase();
  for (const [k, v] of Object.entries(CITY_TO_PROVINCE)) {
    if (k.toLowerCase() === lower) return v;
  }
  return null;
}

export interface ProvinceMeta {
  name: string;
  center: [number, number];
  centroid: [number, number];
  /** Approx east–west span in degrees (cos-corrected). */
  spanLng: number;
  /** Approx north–south span in degrees. */
  spanLat: number;
  /** Dominant visual span used for equal-size framing. */
  span: number;
}

interface ChinaGeoJson {
  features: Array<{
    properties?: {
      name?: string;
      center?: [number, number];
      centroid?: [number, number];
    };
    geometry?: {
      type?: string;
      coordinates?: unknown;
    };
  }>;
}

function walkCoords(coords: unknown, visit: (lng: number, lat: number) => void) {
  if (!Array.isArray(coords) || coords.length === 0) return;
  if (typeof coords[0] === "number" && typeof coords[1] === "number") {
    visit(coords[0] as number, coords[1] as number);
    return;
  }
  for (const c of coords) walkCoords(c, visit);
}

function bboxFromGeometry(geometry: ChinaGeoJson["features"][0]["geometry"]): {
  minLng: number;
  maxLng: number;
  minLat: number;
  maxLat: number;
} | null {
  if (!geometry?.coordinates) return null;
  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  walkCoords(geometry.coordinates, (lng, lat) => {
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  });
  if (!Number.isFinite(minLng)) return null;
  return { minLng, maxLng, minLat, maxLat };
}

/**
 * ECharts map zoom is roughly inverse to geographic span shown.
 * Pick zoom so each province fills a similar fraction of the left pane.
 */
export function focusZoomForProvince(meta: ProvinceMeta): number {
  // Empirically: at zoom≈1 China (~55° span) fills the view.
  const CHINA_VIEW_SPAN = 52;
  const FILL = 0.48; // province occupies ~half the view
  const raw = (CHINA_VIEW_SPAN * FILL) / Math.max(meta.span, 0.35);
  return Math.min(14, Math.max(1.35, raw));
}

export function parseProvinceMeta(geo: ChinaGeoJson): Map<string, ProvinceMeta> {
  const map = new Map<string, ProvinceMeta>();
  for (const f of geo.features) {
    const name = String(f.properties?.name || "");
    if (!name) continue;
    const center = f.properties?.center || f.properties?.centroid;
    const centroid = f.properties?.centroid || f.properties?.center;
    if (!center || !centroid) continue;

    const box = bboxFromGeometry(f.geometry);
    let spanLng = 4;
    let spanLat = 4;
    if (box) {
      const midLat = ((box.minLat + box.maxLat) / 2) * (Math.PI / 180);
      spanLng = Math.max(0.2, (box.maxLng - box.minLng) * Math.cos(midLat));
      spanLat = Math.max(0.2, box.maxLat - box.minLat);
    }
    const span = Math.max(spanLng, spanLat);

    map.set(name, { name, center, centroid, spanLng, spanLat, span });
  }
  return map;
}
