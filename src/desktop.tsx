import { listen } from "@tauri-apps/api/event";
import { engineBaseUrl, enginePort } from "./engine-url";

const status = document.getElementById("status");
let redirected = false;

function setStatus(text: string) {
  if (status) status.textContent = text;
}

function openHub(port: number) {
  if (redirected) return;
  redirected = true;
  const url = `${engineBaseUrl(port)}/`;
  setStatus(`Connecting to camera hub on port ${port}…`);
  window.location.replace(url);
}

async function probe(port: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 800);
  try {
    const res = await fetch(`${engineBaseUrl(port)}/api/telemetry`, {
      signal: controller.signal,
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

async function boot() {
  const initial = enginePort();
  if (await probe(initial)) {
    openHub(initial);
    return;
  }

  window.addEventListener("inbound-engine-ready", ((event: CustomEvent<number>) => {
    if (typeof event.detail === "number") openHub(event.detail);
  }) as EventListener);

  try {
    await listen<number>("engine-ready", (event) => {
      openHub(event.payload);
    });
  } catch {
    // Running outside Tauri (plain Vite). Poll the default engine port.
  }

  setStatus("Starting local camera engine…");
  const started = Date.now();
  const poll = window.setInterval(async () => {
    if (redirected) {
      window.clearInterval(poll);
      return;
    }
    if (await probe(enginePort())) {
      window.clearInterval(poll);
      openHub(enginePort());
      return;
    }
    if (Date.now() - started > 90_000) {
      window.clearInterval(poll);
      setStatus("Engine did not start. Run python edge/launcher.py --no-browser and retry.");
    }
  }, 500);
}

void boot();
