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
  const detections = [...state.detections].sort((a, b) => b.ts - a.ts);

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
        {detections.map((detection) => (
          <Scene key={detection.id} detection={detection} />
        ))}
      </div>
    </section>
  );
}
