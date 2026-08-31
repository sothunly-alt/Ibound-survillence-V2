import { useEffect, useState } from "react";
import { engineBaseUrl } from "../../engine-url";
import { cameraById, confidenceLabel, formatRelative } from "../format";
import { holdLabel, holdReason, lastAlertByCamera } from "../rules";
import { useOps } from "../store";
import type { Detection } from "../types";

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
        <span className="frame__tag">{camera?.name} · {camera?.rtspLabel}</span>
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

export function LiveView() {
  const { state } = useOps();
  const [engineLive, setEngineLive] = useState(false);
  const detections = [...state.detections].sort((a, b) => b.ts - a.ts);
  const engine = engineBaseUrl();
  const engineStream = `${engine}/api/stream`;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 800);
    fetch(`${engine}/api/telemetry`, { cache: "no-store", signal: controller.signal })
      .then((res) => {
        if (!cancelled && res.ok) setEngineLive(true);
      })
      .catch(() => {})
      .finally(() => window.clearTimeout(timer));
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [engine]);

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
              <span className="frame__tag">Edge engine · {engine}</span>
            </div>
            <div className="frame__meta">
              <strong>Live MJPEG from the local camera engine</strong>
              <span>YOLO11 pose overlay and till ROI are drawn on the engine.</span>
            </div>
          </article>
        ) : null}
        {detections.map((detection) => (
          <Scene key={detection.id} detection={detection} />
        ))}
      </div>
    </section>
  );
}
