import { useEffect, useMemo, useState } from "react";
import { engineBaseUrl } from "../../engine-url";
import { engineBayToStation, type StationBay } from "../bay-names";
import { cameraById, confidenceLabel, formatRelative } from "../format";
import { holdLabel, holdReason, lastAlertByCamera } from "../rules";
import { useOps } from "../store";
import type { Detection, EngineTelemetry } from "../types";
import { BayContextMenu } from "./BayContextMenu";
import { DiscoveryModal } from "./DiscoveryModal";

function Scene({ detection }: { detection: Detection }) {
  const { state } = useOps();
  const last = lastAlertByCamera(state.alerts);
  const hold = holdReason(detection, state.rules, last.get(detection.cameraId));
  const camera = cameraById(state.cameras, detection.cameraId);

  return (
    <article className="frame">
      <div className="frame__scene">
        <div
          className={`bbox${hold ? " is-held" : ""}`}
          style={{
            left: `${detection.bbox.x}%`,
            top: `${detection.bbox.y}%`,
            width: `${detection.bbox.w}%`,
            height: `${detection.bbox.h}%`,
          }}
        />
        <span className="frame__tag">
          {camera?.name} · {camera?.protocol ? `${camera.protocol} · ` : ""}{camera?.rtspLabel}
        </span>
      </div>
      <div className="frame__meta">
        <strong>
          {detection.objectClass} · {confidenceLabel(detection.confidence)}
        </strong>
        <span>
          {camera?.zone} · {formatRelative(detection.ts)} · {detection.id}
        </span>
        <span className={`pill${hold ? " is-held" : ""}`}>{holdLabel(hold)}</span>
      </div>
    </article>
  );
}

function formatIngest(tel: EngineTelemetry | null): string {
  if (!tel) return "— fps";
  const raw = tel.ingest_fps != null ? tel.ingest_fps : tel.fps;
  if (raw == null || Number.isNaN(Number(raw))) return "— fps";
  return `${Number(raw).toFixed(1)} fps`;
}

function formatInfer(tel: EngineTelemetry | null): string {
  if (tel?.infer_ms == null || Number.isNaN(Number(tel.infer_ms))) return "— ms";
  return `${Math.round(Number(tel.infer_ms))} ms`;
}

function formatResolution(tel: EngineTelemetry | null): string {
  if (!tel) return "—";
  if (tel.resolution) return tel.resolution;
  if (tel.width && tel.height) return `${tel.width}x${tel.height}`;
  return "—";
}

export function LiveView() {
  const { state } = useOps();
  const [engineLive, setEngineLive] = useState(false);
  const [scanOpen, setScanOpen] = useState(false);
  const [telemetry, setTelemetry] = useState<EngineTelemetry | null>(null);
  const [selectedBayId, setSelectedBayId] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const detections = [...state.detections].sort((a, b) => b.ts - a.ts);
  const engine = engineBaseUrl();
  const engineStream = `${engine}/api/stream`;
  const bays = useMemo(() => {
    const rows = telemetry?.bays || [];
    return rows
      .map((row) => engineBayToStation(row as Record<string, unknown>))
      .filter((row): row is StationBay => row != null);
  }, [telemetry]);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const controller = new AbortController();
      const abortTimer = window.setTimeout(() => controller.abort(), 1200);
      try {
        const res = await fetch(`${engine}/api/telemetry`, { cache: "no-store", signal: controller.signal });
        if (cancelled) return;
        if (!res.ok) {
          setEngineLive(false);
          return;
        }
        const data = (await res.json()) as EngineTelemetry;
        setEngineLive(true);
        setTelemetry(data);
      } catch {
        if (!cancelled) setEngineLive(false);
      } finally {
        window.clearTimeout(abortTimer);
      }
    }
    void tick();
    const interval = window.setInterval(() => void tick(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [engine]);

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") {
        setMenu(null);
        return;
      }
      if (ev.key !== "Delete" && ev.key !== "Backspace") return;
      const tag = ((ev.target as HTMLElement | null)?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if ((ev.target as HTMLElement | null)?.isContentEditable) return;
      if (!selectedBayId) return;
      ev.preventDefault();
      void deleteBay(selectedBayId);
    }
    function onDown(ev: MouseEvent) {
      const target = ev.target as HTMLElement | null;
      if (target?.closest?.("#bay-context-menu")) return;
      setMenu(null);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [selectedBayId, bays, engine]);

  async function persistBays(next: StationBay[]) {
    const res = await fetch(`${engine}/api/garage/bays`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bays: next }),
    });
    const data = (await res.json()) as { bays?: Record<string, unknown>[] };
    if (Array.isArray(data.bays)) {
      setTelemetry((prev) => ({
        ...(prev || {}),
        bays: data.bays,
      }));
    }
  }

  async function deleteBay(bayId: string) {
    setMenu(null);
    const bay = bays.find((item) => item.id === bayId);
    if (!bay) return;
    if (!window.confirm(`Delete "${bay.name}"? Past reports keep their history.`)) return;
    const next = bays.filter((item) => item.id !== bayId);
    setSelectedBayId(next[0]?.id || "");
    await persistBays(next);
  }

  function renameBay() {
    setMenu(null);
    const bay = bays.find((item) => item.id === selectedBayId);
    if (!bay) return;
    const label = window.prompt("Rename bay", bay.name);
    if (label == null) return;
    const nextName = label.trim();
    if (!nextName) return;
    void persistBays(
      bays.map((item) =>
        item.id === bay.id
          ? { ...item, name: nextName, type: /tool/i.test(nextName) ? "tool_area" : item.type }
          : item,
      ),
    );
  }

  function toggleBayType() {
    setMenu(null);
    const bay = bays.find((item) => item.id === selectedBayId);
    if (!bay) return;
    void persistBays(
      bays.map((item) =>
        item.id === bay.id
          ? { ...item, type: item.type === "tool_area" ? "vehicle_bay" : "tool_area" }
          : item,
      ),
    );
  }

  const proto = (telemetry?.protocol || "").trim();
  const hasMain = Boolean(telemetry?.main_stream);

  return (
    <section className="panel">
      <header className="panel__head">
        <div>
          <h2>Live inference</h2>
          <p>
            Mocked RTSP ingest at 2 fps. YOLO returns class, bounding box, and confidence.
            Only detections that pass the rule engine are packaged as alerts.
          </p>
        </div>
        <div className="live-badges">
          {engineLive ? (
            <>
              <span className="live-badge is-proto">{proto ? proto.toUpperCase() : "ENGINE"}</span>
              <span className="live-badge">{formatResolution(telemetry)}</span>
              <span className="live-badge">{formatIngest(telemetry)}</span>
              <span className="live-badge">{formatInfer(telemetry)}</span>
              {hasMain ? <span className="live-badge">MAIN</span> : null}
            </>
          ) : null}
          <button className="btn btn--ghost btn--sm" type="button" onClick={() => setScanOpen(true)}>
            Scan network
          </button>
        </div>
      </header>
      <div className="grid-detect">
        {engineLive ? (
          <article className="frame">
            <div className="frame__scene">
              <img
                src={engineStream}
                alt="Live camera engine"
                onError={() => setEngineLive(false)}
              />
              {bays.map((bay) => {
                const [x, y, w, h] = bay.roi;
                return (
                  <button
                    key={bay.id}
                    type="button"
                    className={`roi-hotspot${bay.id === selectedBayId ? " is-selected" : ""}`}
                    style={{
                      left: `${x * 100}%`,
                      top: `${y * 100}%`,
                      width: `${w * 100}%`,
                      height: `${h * 100}%`,
                    }}
                    onClick={() => setSelectedBayId(bay.id)}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      setSelectedBayId(bay.id);
                      setMenu({ x: event.clientX, y: event.clientY });
                    }}
                    aria-label={bay.name}
                    title={bay.name}
                  />
                );
              })}
              <span className="frame__tag">
                Edge engine · {engine}
                {proto ? ` · ${proto}` : ""}
              </span>
            </div>
            <div className="frame__meta">
              <strong>Live MJPEG from the local camera engine</strong>
              <span>
                {formatResolution(telemetry)} · ingest {formatIngest(telemetry)} · infer {formatInfer(telemetry)}
              </span>
            </div>
          </article>
        ) : null}
        {detections.map((detection) => (
          <Scene key={detection.id} detection={detection} />
        ))}
      </div>
      <DiscoveryModal
        open={scanOpen}
        engineBase={engine}
        onClose={() => setScanOpen(false)}
      />
      <BayContextMenu
        open={Boolean(menu)}
        x={menu?.x || 0}
        y={menu?.y || 0}
        onRename={renameBay}
        onToggleType={toggleBayType}
        onDelete={() => void deleteBay(selectedBayId)}
      />
    </section>
  );
}
