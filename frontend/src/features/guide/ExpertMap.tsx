import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import * as echarts from "echarts/core";
import { MapChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsType } from "echarts/core";
import { Button } from "../../components/ui/Button";
import { useI18n } from "../../i18n/i18n";
import { expertPath } from "../../lib/diseasePath";
import { haversineKm } from "../../lib/geo";
import { cityToProvince, focusZoomForProvince, parseProvinceMeta, type ProvinceMeta } from "../../lib/province";
import type { GuideExpert, Lang } from "../../types";
import styles from "./expertMap.module.css";

echarts.use([MapChart, TooltipComponent, CanvasRenderer]);

interface ExpertMapProps {
  diseaseId: string;
  experts: GuideExpert[];
  userPos: { lat: number; lng: number } | null;
  geoMode: "locating" | "located" | "city" | "denied" | "unavailable";
  onRequestGeo: () => void;
}

type Phase = "overview" | "focus";

interface FanExpert extends GuideExpert {
  province: string;
}

const VISIBLE_SLOTS = 2;
const ANGLE_STEP = 0.67;
const FAN_RADIUS = 96;
const MAP_ZOOM_MS = 900;
const FOCUS_ANCHOR_DEFAULT_X = 18;
const FAN_WIDTH_RATIO = 0.58;
const FAN_MAX_WIDTH = 480;
const FAN_SHIFT_PX = -28;
const CHINA_CENTER: [number, number] = [104.2, 35.5];
const HOVER_GREEN = "#0a3d2a";
const FADE_KM = 1600;

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n));
}

function dockScale(offset: number): number {
  const a = Math.abs(offset);
  if (a >= VISIBLE_SLOTS + 0.6) return 0.78;
  return clamp(1.2 - a * 0.14, 0.78, 1.22);
}

function dockOpacity(offset: number): number {
  const a = Math.abs(offset);
  if (a < 0.4) return 1;
  return clamp(1 - a * 0.4, 0.14, 0.92);
}

let chinaMapRegistered = false;

async function ensureChinaMap(): Promise<Map<string, ProvinceMeta>> {
  const res = await fetch("/geo/china.json");
  if (!res.ok) throw new Error("Failed to load china map");
  const geo = await res.json();
  if (!chinaMapRegistered) {
    echarts.registerMap("china", geo);
    chinaMapRegistered = true;
  }
  return parseProvinceMeta(geo);
}

const PROVINCE_EN: Record<string, string> = {
  北京市: "Beijing",
  天津市: "Tianjin",
  河北省: "Hebei",
  山西省: "Shanxi",
  内蒙古自治区: "Inner Mongolia",
  辽宁省: "Liaoning",
  吉林省: "Jilin",
  黑龙江省: "Heilongjiang",
  上海市: "Shanghai",
  江苏省: "Jiangsu",
  浙江省: "Zhejiang",
  安徽省: "Anhui",
  福建省: "Fujian",
  江西省: "Jiangxi",
  山东省: "Shandong",
  河南省: "Henan",
  湖北省: "Hubei",
  湖南省: "Hunan",
  广东省: "Guangdong",
  广西壮族自治区: "Guangxi",
  海南省: "Hainan",
  重庆市: "Chongqing",
  四川省: "Sichuan",
  贵州省: "Guizhou",
  云南省: "Yunnan",
  西藏自治区: "Tibet",
  陕西省: "Shaanxi",
  甘肃省: "Gansu",
  青海省: "Qinghai",
  宁夏回族自治区: "Ningxia",
  新疆维吾尔自治区: "Xinjiang",
  台湾省: "Taiwan",
  香港特别行政区: "Hong Kong",
  澳门特别行政区: "Macao",
};

function shortProvinceLabel(name: string): string {
  return name
    .replace(/壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区/g, "")
    .replace(/省|市/g, "");
}

function provinceLabel(name: string, lang: Lang): string {
  if (lang === "en") return PROVINCE_EN[name] || name;
  return shortProvinceLabel(name);
}

function provinceOpacity(
  name: string,
  selected: string | null,
  focused: boolean,
  metaMap: Map<string, ProvinceMeta>,
): number {
  if (!focused || !selected) return 1;
  if (name === selected) return 1;
  const a = metaMap.get(selected);
  const b = metaMap.get(name);
  if (!a || !b) return 0.12;
  const [lngA, latA] = a.centroid;
  const [lngB, latB] = b.centroid;
  const d = haversineKm(latA, lngA, latB, lngB);
  return clamp(1 - d / FADE_KM, 0.05, 0.42);
}

function buildProvinceData(
  metaMap: Map<string, ProvinceMeta>,
  activeProvinces: Set<string>,
  byProvince: Map<string, FanExpert[]>,
  selected: string | null,
  phase: Phase,
  fadeOthers: boolean,
  formatProvince: (name: string) => string,
) {
  const focused = phase === "focus" && !!selected;
  return [...metaMap.keys()].map((name) => {
    const hasExperts = activeProvinces.has(name);
    const isSel = name === selected;
    const opacity =
      focused && fadeOthers && selected
        ? provinceOpacity(name, selected, true, metaMap)
        : 1;
    return {
      name,
      value: byProvince.get(name)?.length || 0,
      itemStyle: {
        areaColor: isSel ? "#0f5138" : hasExperts ? "#7fa892" : "#ebe4d4",
        borderColor: isSel ? "#b8d92e" : "#cfc5af",
        borderWidth: isSel ? 1.8 : 0.6,
        opacity: isSel ? 1 : opacity,
      },
      label: {
        show: isSel || (phase === "overview" && hasExperts),
        color: isSel ? "#e9f5ec" : "#3a4640",
        fontSize: isSel ? 13 : 10,
        formatter: () => formatProvince(name),
      },
      emphasis: {
        disabled: focused ? !isSel : !hasExperts,
      },
    };
  });
}

interface MapViewState {
  center: [number, number];
  zoom: number;
  layoutCenter: [string, string];
  layoutSize: string;
}

function readCurrentMapView(chart: EChartsType): MapViewState | null {
  const opt = chart.getOption() as {
    series?: Array<{
      center?: unknown;
      zoom?: unknown;
      layoutCenter?: unknown;
      layoutSize?: unknown;
    }>;
  };
  const raw = opt.series?.[0];
  if (!raw) return null;

  const centerRaw = Array.isArray(raw.center) && Array.isArray(raw.center[0])
    ? raw.center[0]
    : raw.center;
  if (!Array.isArray(centerRaw) || centerRaw.length < 2) return null;
  const center: [number, number] = [Number(centerRaw[0]), Number(centerRaw[1])];
  if (!Number.isFinite(center[0]) || !Number.isFinite(center[1])) return null;

  const zoomRaw = Array.isArray(raw.zoom) ? raw.zoom[0] : raw.zoom;
  const zoom = Number(zoomRaw);
  if (!Number.isFinite(zoom)) return null;

  const lcRaw = Array.isArray(raw.layoutCenter) && Array.isArray(raw.layoutCenter[0])
    ? raw.layoutCenter[0]
    : raw.layoutCenter;
  const layoutCenter: [string, string] =
    Array.isArray(lcRaw) && lcRaw.length >= 2
      ? [String(lcRaw[0]), String(lcRaw[1])]
      : ["50%", "50%"];

  const lsRaw = Array.isArray(raw.layoutSize) ? raw.layoutSize[0] : raw.layoutSize;
  const layoutSize = lsRaw != null ? String(lsRaw) : "96%";

  return { center, zoom, layoutCenter, layoutSize };
}

export function ExpertMap({
  diseaseId,
  experts,
  userPos,
  geoMode,
  onRequestGeo,
}: ExpertMapProps) {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const provinceMetaRef = useRef<Map<string, ProvinceMeta>>(new Map());
  const stageRef = useRef<HTMLDivElement>(null);
  const byProvinceRef = useRef(new Map<string, FanExpert[]>());
  const activeProvincesRef = useRef(new Set<string>());
  const fanLenRef = useRef(0);
  const phaseRef = useRef<Phase>("overview");
  const snapTimer = useRef<number | null>(null);
  const pendingFocusStartRef = useRef<MapViewState | null>(null);

  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("overview");
  const [selected, setSelected] = useState<string | null>(null);
  const [activeFloat, setActiveFloat] = useState(0);
  const [fanReady, setFanReady] = useState(false);
  const [locateArmed, setLocateArmed] = useState(false);
  const [focusAnchorX, setFocusAnchorX] = useState(FOCUS_ANCHOR_DEFAULT_X);

  const byProvince = useMemo(() => {
    const map = new Map<string, FanExpert[]>();
    for (const ex of experts) {
      const province = cityToProvince(ex.hospital.city);
      if (!province) continue;
      const list = map.get(province) || [];
      list.push({ ...ex, province });
      map.set(province, list);
    }
    return map;
  }, [experts]);

  const activeProvinces = useMemo(() => new Set(byProvince.keys()), [byProvince]);
  byProvinceRef.current = byProvince;
  activeProvincesRef.current = activeProvinces;

  const fanExperts = useMemo(() => {
    if (!selected) return [] as FanExpert[];
    return byProvince.get(selected) || [];
  }, [byProvince, selected]);

  fanLenRef.current = fanExperts.length;
  phaseRef.current = phase;

  const activeIndex = clamp(Math.round(activeFloat), 0, Math.max(0, fanExperts.length - 1));

  const focusProvince = (name: string) => {
    if (!byProvinceRef.current.has(name)) return;
    if (chartRef.current) {
      pendingFocusStartRef.current = readCurrentMapView(chartRef.current);
    }
    setFanReady(false);
    setSelected(name);
    setPhase("focus");
    setActiveFloat(0);
  };

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const recompute = () => {
      const width = stage.clientWidth;
      if (!width) return;
      if (window.matchMedia("(max-width: 860px)").matches) {
        setFocusAnchorX(50);
        return;
      }
      const fanWidth = Math.min(width * FAN_WIDTH_RATIO, FAN_MAX_WIDTH);
      const pivotPx = width - fanWidth + FAN_SHIFT_PX - 18;
      const anchor = clamp((pivotPx / width) * 100, 8, 30);
      setFocusAnchorX(Number(anchor.toFixed(2)));
    };

    recompute();
    const ro = new ResizeObserver(recompute);
    ro.observe(stage);
    return () => ro.disconnect();
  }, []);

  const focusNearestFromPos = (pos: { lat: number; lng: number }) => {
    let best: string | null = null;
    let bestKm = Infinity;
    for (const name of activeProvincesRef.current) {
      const meta = provinceMetaRef.current.get(name);
      if (!meta) continue;
      const [lng, lat] = meta.centroid;
      const d = haversineKm(pos.lat, pos.lng, lat, lng);
      if (d < bestKm) {
        bestKm = d;
        best = name;
      }
    }
    if (best) focusProvince(best);
  };

  const goToIndex = (i: number) => {
    setActiveFloat(clamp(i, 0, Math.max(0, fanLenRef.current - 1)));
  };

  const openExpert = (ex: FanExpert, i: number) => {
    const centered = Math.abs(i - activeFloat) < 0.45;
    if (!centered) {
      goToIndex(i);
      return;
    }
    navigate(expertPath(diseaseId, ex.id));
  };

  useEffect(() => {
    let disposed = false;
    let chart: EChartsType | null = null;
    let ro: ResizeObserver | null = null;

    ensureChinaMap()
      .then((meta) => {
        if (disposed || !hostRef.current) return;
        provinceMetaRef.current = meta;
        chart = echarts.init(hostRef.current, undefined, { renderer: "canvas" });
        chartRef.current = chart;

        chart.on("click", (params) => {
          const name = String(params.name || "");
          if (!name || !activeProvincesRef.current.has(name)) return;
          focusProvince(name);
        });

        ro = new ResizeObserver(() => chart?.resize());
        ro.observe(hostRef.current);
        setReady(true);
        setMapError(null);
      })
      .catch((err) => {
        if (!disposed) setMapError((err as Error).message || "map error");
      });

    return () => {
      disposed = true;
      ro?.disconnect();
      chart?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !ready) return;

    const metaMap = provinceMetaRef.current;
    const meta = selected ? metaMap.get(selected) : null;
    const focused = phase === "focus" && !!meta;
    const focusCenter: [string, string] = [`${focusAnchorX}%`, "50%"];

    const paint = (fadeOthers: boolean, duration: number, easing: "linear" | "cubicOut") => {
      chart.setOption({
        animationDurationUpdate: duration,
        animationEasingUpdate: easing,
        tooltip: {
          show: phase === "overview",
          trigger: "item",
          formatter: (p: { name?: string }) => {
            const name = p.name || "";
            const n = byProvince.get(name)?.length || 0;
            const label = provinceLabel(name, lang);
            if (!n) return `${label}<br/>${t("map_no_experts")}`;
            return `${label}<br/>${t("map_expert_count").replace("{n}", String(n))}`;
          },
        },
        series: [
          {
            type: "map",
            map: "china",
            roam: phase === "overview",
            selectedMode: false,
            layoutCenter: focused ? focusCenter : ["50%", "50%"],
            layoutSize: focused ? "92%" : "96%",
            center: focused && meta ? meta.centroid : CHINA_CENTER,
            zoom: focused && meta ? focusZoomForProvince(meta) : 1.05,
            itemStyle: {
              areaColor: "#ebe4d4",
              borderColor: "#cfc5af",
              borderWidth: 0.7,
            },
            emphasis: {
              label: { show: true, color: "#e9f5ec" },
              itemStyle: { areaColor: HOVER_GREEN },
            },
            select: { disabled: true },
            data: buildProvinceData(
              metaMap,
              activeProvinces,
              byProvince,
              selected,
              phase,
              fadeOthers,
              (name) => provinceLabel(name, lang),
            ),
          },
        ],
      });
    };

    chart.resize();
    const pending = focused ? pendingFocusStartRef.current : null;
    if (pending && focused) {
      chart.setOption({
        animation: false,
        series: [
          {
            type: "map",
            map: "china",
            roam: false,
            selectedMode: false,
            layoutCenter: pending.layoutCenter,
            layoutSize: pending.layoutSize,
            center: pending.center,
            zoom: pending.zoom,
            data: buildProvinceData(
              metaMap,
              activeProvinces,
              byProvince,
              selected,
              phase,
              false,
              (name) => provinceLabel(name, lang),
            ),
          },
        ],
      });
    }
    pendingFocusStartRef.current = null;
    // Step 1: linear zoom/pan only — no province fade yet
    paint(false, MAP_ZOOM_MS, "linear");

    let fadeTimer: number | undefined;
    if (focused) {
      // Step 2: fade surrounding provinces after zoom completes
      fadeTimer = window.setTimeout(() => paint(true, 380, "cubicOut"), MAP_ZOOM_MS);
    }

    return () => {
      if (fadeTimer) window.clearTimeout(fadeTimer);
    };
  }, [ready, phase, selected, activeProvinces, byProvince, t, lang, focusAnchorX]);

  // Reveal dial after the map zoom finishes
  useEffect(() => {
    if (phase !== "focus") {
      setFanReady(false);
      return;
    }
    setFanReady(false);
    const id = window.setTimeout(() => setFanReady(true), MAP_ZOOM_MS + 60);
    return () => window.clearTimeout(id);
  }, [phase, selected]);

  useEffect(() => {
    if (!locateArmed || !userPos || !ready) return;
    focusNearestFromPos(userPos);
    setLocateArmed(false);
  }, [locateArmed, userPos, ready]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const handler = (e: globalThis.WheelEvent) => {
      if (phaseRef.current !== "focus" || fanLenRef.current < 2) return;
      e.preventDefault();
      setActiveFloat((prev) =>
        clamp(prev + e.deltaY * 0.0038, 0, fanLenRef.current - 1),
      );
      if (snapTimer.current) window.clearTimeout(snapTimer.current);
      snapTimer.current = window.setTimeout(() => {
        setActiveFloat((prev) => Math.round(prev));
      }, 120);
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => {
      el.removeEventListener("wheel", handler);
      if (snapTimer.current) window.clearTimeout(snapTimer.current);
    };
  }, [ready, phase]);

  const locateNearest = () => {
    if (userPos && ready) {
      focusNearestFromPos(userPos);
      return;
    }
    setLocateArmed(true);
    onRequestGeo();
  };

  const resetOverview = () => {
    setFanReady(false);
    setPhase("overview");
    setSelected(null);
    setActiveFloat(0);
  };

  return (
    <div className={`${styles.wrap} ${phase === "focus" ? styles.focus : ""}`}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarText}>
          <span className={styles.hint}>
            {phase === "overview" ? t("map_hint_pick") : t("map_hint_wheel")}
          </span>
          {geoMode === "locating" && (
            <span className={styles.status}>{t("guide_locating")}</span>
          )}
          {geoMode === "located" && selected && (
            <span className={styles.status}>
              {t("map_located_province").replace("{p}", provinceLabel(selected, lang))}
            </span>
          )}
        </div>
        <div className={styles.toolbarActions}>
          {phase === "focus" && (
            <Button variant="ghost" type="button" onClick={resetOverview}>
              {t("map_back_china")}
            </Button>
          )}
          <Button
            variant="brand"
            type="button"
            onClick={locateNearest}
            loading={geoMode === "locating" && locateArmed}
          >
            {t("map_locate")}
          </Button>
        </div>
      </div>

      <div
        className={styles.stage}
        ref={stageRef}
        style={
          {
            "--focus-anchor-x": `${focusAnchorX}%`,
            "--fan-shift-px": `${FAN_SHIFT_PX}px`,
          } as CSSProperties
        }
      >
        <div className={styles.mapPane}>
          {mapError ? (
            <div className={styles.mapFallback}>{mapError}</div>
          ) : (
            <div ref={hostRef} className={styles.mapHost} role="img" aria-label={t("map_aria")} />
          )}
          <div
            className={`${styles.mapVignette} ${phase === "focus" ? styles.mapVignetteOn : ""}`}
            aria-hidden
          />
        </div>

        {phase === "focus" && fanReady && (
          <div className={`${styles.fanPane} ${styles.fanPaneReady}`} aria-live="polite">
            <div className={styles.fanOrbit}>
              {fanExperts.map((ex, i) => {
                const offset = i - activeFloat;
                const absOff = Math.abs(offset);
                if (absOff > VISIBLE_SLOTS + 0.55) return null;

                const angle = offset * ANGLE_STEP;
                const x = Math.cos(angle) * FAN_RADIUS;
                // Stretch vertical spacing so compact cards don't stack tight
                const y = Math.sin(angle) * FAN_RADIUS * 1.55;
                const opacity = dockOpacity(offset);
                const scale = dockScale(offset);
                const isCenter = absOff < 0.42;

                return (
                  <button
                    key={ex.id}
                    type="button"
                    className={`${styles.fanCard} ${isCenter ? styles.fanCardCenter : ""}`}
                    style={{
                      opacity,
                      transform: `translate3d(${x}px, ${y}px, 0) translateY(-50%) scale(${scale})`,
                      zIndex: isCenter ? 28 : Math.round(18 - absOff * 4),
                      pointerEvents: absOff > VISIBLE_SLOTS ? "none" : "auto",
                    }}
                    onClick={() => openExpert(ex, i)}
                    aria-current={isCenter ? "true" : undefined}
                  >
                    <div className={styles.fanHead}>
                      {ex.hospital.logo_url ? (
                        <img
                          className={styles.fanLogo}
                          src={ex.hospital.logo_url}
                          alt=""
                          loading="lazy"
                        />
                      ) : (
                        <div className={styles.fanLogoFallback} aria-hidden>
                          {t("guide_logo_soon")}
                        </div>
                      )}
                      <div className={styles.fanMeta}>
                        <div className={styles.fanName}>{ex.name}</div>
                        <div className={styles.fanHospital}>{ex.hospital.name}</div>
                      </div>
                      {isCenter && (
                        <span className={styles.openHint}>{t("map_open_detail")}</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>

            {fanExperts.length > 1 && (
              <div className={styles.dots} role="tablist" aria-label={t("map_hint_wheel")}>
                {fanExperts.map((ex, i) => (
                  <button
                    key={ex.id}
                    type="button"
                    className={`${styles.dot} ${i === activeIndex ? styles.dotActive : ""}`}
                    onClick={() => goToIndex(i)}
                    aria-label={ex.name}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {phase === "overview" && (
        <ul className={styles.legend}>
          {[...byProvince.entries()]
            .sort((a, b) => a[0].localeCompare(b[0], "zh"))
            .map(([name, list]) => (
              <li key={name}>
                <button
                  type="button"
                  className={styles.legendBtn}
                  onClick={() => focusProvince(name)}
                >
                  <span className={styles.legendMark} />
                  {provinceLabel(name, lang)}
                  <span className={styles.legendN}>{list.length}</span>
                </button>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
