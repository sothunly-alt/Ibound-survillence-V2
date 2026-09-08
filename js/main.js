const header = document.getElementById("header");
const mobileNav = document.getElementById("mobile-nav");
const menuToggle = document.querySelector("[data-menu-toggle]");
const modal = document.getElementById("demo-modal");
const form = document.getElementById("demo-form");
const success = document.getElementById("form-success");
const planSelect = document.getElementById("plan-select");

const pad = (n) => String(n).padStart(2, "0");

function formatHms(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function tickClock(el, start = new Date()) {
  const render = () => {
    const now = new Date(start.getTime() + (Date.now() - start.getTime()));
    el.textContent = [now.getHours(), now.getMinutes(), now.getSeconds()].map(pad).join(":");
  };
  render();
  return setInterval(render, 1000);
}

document.querySelectorAll("[data-hud-clock]").forEach((el) => tickClock(el));

const heroTimer = document.querySelector("[data-hero-timer]");
if (heroTimer) {
  let seconds = 2 * 3600 + 14 * 60 + 33;
  setInterval(() => {
    seconds += 1;
    heroTimer.textContent = formatHms(seconds);
  }, 1000);
}

const bbox = document.querySelector("[data-bbox]");
const bboxTimer = document.querySelector("[data-bbox-timer]");
const phone = document.querySelector("[data-phone]");
const demoSection = document.querySelector(".demo-section");
const steps = [...document.querySelectorAll("[data-flow-step]")];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let demoSeconds = 4 * 3600 + 11 * 60 + 55;
let demoPhase = 0;

function setPhase(phase) {
  demoPhase = phase;
  steps.forEach((step) => step.classList.toggle("is-active", Number(step.dataset.flowStep) === phase));
  bbox?.classList.toggle("is-on", phase >= 1);
  phone?.classList.toggle("is-on", phase >= 2);
  demoSection?.classList.toggle("is-phone-focus", phase >= 2);
}

function runDemoLoop() {
  setPhase(1);
  demoSeconds = 4 * 3600 + 11 * 60 + 55;
  if (bboxTimer) bboxTimer.textContent = formatHms(demoSeconds);

  const t2 = setTimeout(() => setPhase(2), 4800);
  const t3 = setTimeout(runDemoLoop, 11000);
  return () => [t2, t3].forEach(clearTimeout);
}

if (bboxTimer) {
  setInterval(() => {
    if (demoPhase >= 1 && demoPhase < 2) {
      demoSeconds += 1;
      bboxTimer.textContent = formatHms(demoSeconds);
    }
  }, 1000);
  runDemoLoop();
}

menuToggle?.addEventListener("click", () => {
  const open = mobileNav.hasAttribute("hidden");
  mobileNav.toggleAttribute("hidden", !open);
  menuToggle.setAttribute("aria-expanded", String(open));
});

document.querySelectorAll(".nav-mobile a").forEach((link) => {
  link.addEventListener("click", () => {
    mobileNav.setAttribute("hidden", "");
    menuToggle?.setAttribute("aria-expanded", "false");
  });
});

function openDemo(plan) {
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  if (plan && planSelect) planSelect.value = plan;
  modal.querySelector("input")?.focus();
}

function closeDemo() {
  modal.hidden = true;
  document.body.style.overflow = "";
}

document.querySelectorAll("[data-open-demo]").forEach((btn) => {
  btn.addEventListener("click", () => openDemo(btn.dataset.plan));
});

document.querySelectorAll("[data-close-demo]").forEach((el) => {
  el.addEventListener("click", closeDemo);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !modal.hidden) closeDemo();
});

const DEMO_INBOX = "inboundcrew82@gmail.com";
const formspreeEndpoint = form?.dataset.formspree?.trim() || "";

function resolveLeadEndpoint(raw) {
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  // Allow bare Formspree form IDs: xwqkzabc → https://formspree.io/f/xwqkzabc
  if (/^[a-zA-Z0-9]+$/.test(raw)) return `https://formspree.io/f/${raw}`;
  return raw;
}

function collectDemoPayload(formEl) {
  const data = new FormData(formEl);
  return {
    name: String(data.get("name") || "").trim(),
    shop: String(data.get("shop") || "").trim(),
    contact: String(data.get("contact") || "").trim(),
    bays: String(data.get("bays") || "").trim(),
    plan: String(data.get("plan") || "").trim(),
    operator_ack: data.get("operator_ack") ? "yes" : "no",
  };
}

function buildMailto(payload) {
  const subject = encodeURIComponent(`Demo request — ${payload.shop || payload.name}`);
  const body = encodeURIComponent(
    [
      "Inbound Surveillance — Bay Demo Request",
      "",
      `Name: ${payload.name}`,
      `Shop: ${payload.shop}`,
      `Telegram / phone: ${payload.contact}`,
      `Bays: ${payload.bays}`,
      `Plan: ${payload.plan}`,
      `Operator acknowledgment: ${payload.operator_ack}`,
      "",
      "Sent from the marketing landing demo form.",
    ].join("\n")
  );
  return `mailto:${DEMO_INBOX}?subject=${subject}&body=${body}`;
}

function showFormSuccess() {
  if (form) form.hidden = true;
  if (success) {
    success.hidden = false;
    success.textContent =
      "Request ready. Your email client should open — send it so we can confirm on Telegram within one business day.";
  }
}

function showFormError(message) {
  let err = document.getElementById("form-error");
  if (!err && form) {
    err = document.createElement("p");
    err.id = "form-error";
    err.className = "form-error";
    err.setAttribute("role", "alert");
    form.appendChild(err);
  }
  if (err) {
    err.hidden = false;
    err.textContent = message;
  }
}

form?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const submitBtn = form.querySelector('[type="submit"]');
  const payload = collectDemoPayload(form);
  const existingErr = document.getElementById("form-error");
  if (existingErr) existingErr.hidden = true;

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Sending…";
  }

  try {
    const endpoint = resolveLeadEndpoint(formspreeEndpoint);
    if (endpoint) {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          ...payload,
          _subject: `Demo request — ${payload.shop || payload.name}`,
          email: DEMO_INBOX,
        }),
      });
      if (!res.ok) throw new Error(`Form endpoint returned ${res.status}`);
      if (success) {
        success.hidden = false;
        success.textContent =
          "Request received. We’ll confirm on Telegram within one business day.";
      }
      form.hidden = true;
      setTimeout(() => {
        window.location.href = "thank-you.html";
      }, 600);
      return;
    }

    // Reliable capture without a backend: open a prefilled mailto, then thank-you page.
    const mail = document.createElement("a");
    mail.href = buildMailto(payload);
    mail.rel = "noopener";
    document.body.appendChild(mail);
    mail.click();
    mail.remove();
    showFormSuccess();
    setTimeout(() => {
      window.location.assign("thank-you.html");
    }, 700);
  } catch (err) {
    console.error(err);
    showFormError("Send failed. Opening email fallback…");
    const mail = document.createElement("a");
    mail.href = buildMailto(payload);
    document.body.appendChild(mail);
    mail.click();
    mail.remove();
    showFormSuccess();
    setTimeout(() => {
      window.location.assign("thank-you.html");
    }, 700);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Request Demo";
    }
  }
});

const heroImg = document.querySelector(".hero-media img");
const finalImg = document.querySelector(".final-media img");
const navLinks = [...document.querySelectorAll(".nav-desktop a[href^='#']")];
const navSections = navLinks
  .map((link) => document.querySelector(link.getAttribute("href")))
  .filter(Boolean);

function formatMoney(value) {
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

function formatPayback(days) {
  return `DAY ${String(days).padStart(2, "0")}`;
}

function initRoiCalculator() {
  const root = document.querySelector("[data-roi-calc]");
  if (!root) return;

  const baysInput = root.querySelector("[data-roi-bays]");
  const minutesInput = root.querySelector("[data-roi-minutes]");
  const rateInput = root.querySelector("[data-roi-rate]");
  const baysDisplay = root.querySelector("[data-roi-bays-display]");
  const minutesDisplay = root.querySelector("[data-roi-minutes-display]");
  const rateDisplay = root.querySelector("[data-roi-rate-display]");
  const annualEl = root.querySelector("[data-roi-annual]");
  const softwareEl = root.querySelector("[data-roi-software]");
  const paybackEls = document.querySelectorAll("[data-roi-payback], [data-roi-payback-eq]");
  const minutesEq = document.querySelector("[data-roi-minutes-eq]");
  const rateEq = document.querySelector("[data-roi-rate-eq]");
  const monthlyEl = document.querySelector("[data-roi-monthly]");
  const leakCopy = document.querySelector("[data-roi-leak-copy]");

  const render = () => {
    const bays = Number(baysInput?.value || 3);
    const minutes = Number(minutesInput?.value || 45);
    const rate = Number(rateInput?.value || 35);
    const leakHours = minutes / 60;
    const monthlyPerBay = leakHours * rate * 24;
    const annual = monthlyPerBay * 12 * bays;
    const softwareAnnual = 39 * 12 * bays;
    const dailyRecovery = leakHours * rate;
    const paybackDays = Math.max(1, Math.ceil(39 / Math.max(dailyRecovery, 0.01)));
    const payback = formatPayback(paybackDays);

    if (baysDisplay) baysDisplay.textContent = String(bays);
    if (minutesDisplay) minutesDisplay.textContent = `${minutes} min`;
    if (rateDisplay) rateDisplay.textContent = `$${rate}/hr`;
    if (annualEl) annualEl.textContent = formatMoney(annual);
    if (softwareEl) softwareEl.textContent = formatMoney(softwareAnnual);
    if (minutesEq) minutesEq.innerHTML = `~${minutes}<small>min</small>`;
    if (rateEq) rateEq.innerHTML = `$${rate}<small>/hr</small>`;
    if (monthlyEl) monthlyEl.innerHTML = `${formatMoney(monthlyPerBay)}<small>/bay</small>`;
    paybackEls.forEach((el) => {
      el.textContent = payback;
    });
    if (leakCopy) {
      leakCopy.innerHTML = `At your estimate of ${minutes} unbilled minutes/day and $${rate}/hr across ~24 shop days,
        that’s <strong>${formatMoney(monthlyPerBay)} projected per bay, every month.</strong>`;
    }
  };

  baysInput?.addEventListener("input", render);
  minutesInput?.addEventListener("input", render);
  rateInput?.addEventListener("input", render);
  render();
}

initRoiCalculator();

let scrollTick = false;

function updateScroll() {
  const y = window.scrollY;
  header?.classList.toggle("is-scrolled", y > 8);

  const max = document.documentElement.scrollHeight - window.innerHeight;
  const pct = max > 0 ? Math.min(100, (y / max) * 100) : 0;
  document.documentElement.style.setProperty("--scroll", `${pct}%`);

  if (!reducedMotion) {
    if (heroImg) {
      heroImg.style.transform = `translate3d(0, ${y * 0.18}px, 0) scale(1.08)`;
    }
    if (finalImg) {
      const top = finalImg.getBoundingClientRect().top;
      const shift = Math.max(-80, Math.min(80, (window.innerHeight / 2 - top) * 0.08));
      finalImg.style.transform = `translate3d(0, ${shift}px, 0) scale(1.08)`;
    }
  }

  const marker = y + window.innerHeight * 0.35;
  let activeId = null;
  navSections.forEach((section) => {
    if (section.offsetTop <= marker) activeId = section.id;
  });
  navLinks.forEach((link) => {
    link.classList.toggle("is-active", link.getAttribute("href") === `#${activeId}`);
  });
}

function onScrollFrame() {
  if (scrollTick) return;
  scrollTick = true;
  requestAnimationFrame(() => {
    updateScroll();
    scrollTick = false;
  });
}

window.addEventListener("scroll", onScrollFrame, { passive: true });
window.addEventListener("resize", onScrollFrame, { passive: true });
updateScroll();

function initScrollReveal() {
  if (reducedMotion) return;
  const nodes = document.querySelectorAll(
    ".section-head, .hud-frame, .flow-steps, .arch-card, .roi-calc, .roi-card, .feature-card, .price-card, .partners, .testimonial, .final-content"
  );
  nodes.forEach((el, i) => {
    el.classList.add("reveal");
    el.style.transitionDelay = `${(i % 3) * 70}ms`;
  });
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const el = entry.target;
        if (entry.isIntersecting) {
          el.classList.add("is-in");
          el.classList.remove("from-above");
          return;
        }
        el.classList.remove("is-in");
        el.classList.toggle("from-above", entry.boundingClientRect.top < 0);
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
  );
  nodes.forEach((el) => io.observe(el));
}

initScrollReveal();

function easeOutCubic(t) {
  return 1 - (1 - t) ** 3;
}

function formatCount(el, value) {
  const prefix = el.dataset.prefix ?? "";
  const pad = Number(el.dataset.pad || 0);
  const unit = el.dataset.unit;
  const num = pad ? String(value).padStart(pad, "0") : String(value);
  if (unit) {
    el.innerHTML = `${prefix}${num}<small>${unit}</small>`;
    return;
  }
  el.textContent = `${prefix}${num}`;
}

function animateCount(el, to, duration = 1200) {
  if (el._countFrame) cancelAnimationFrame(el._countFrame);
  const start = performance.now();

  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration);
    formatCount(el, Math.round(to * easeOutCubic(t)));
    if (t < 1) el._countFrame = requestAnimationFrame(tick);
  };
  el._countFrame = requestAnimationFrame(tick);
}

function initCountUps() {
  const counters = [...document.querySelectorAll("[data-count]")];
  if (!counters.length) return;

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const el = entry.target;
        const target = Number(el.dataset.count);
        if (entry.isIntersecting) {
          if (reducedMotion) {
            formatCount(el, target);
            return;
          }
          animateCount(el, target);
          return;
        }
        if (el._countFrame) cancelAnimationFrame(el._countFrame);
        formatCount(el, 0);
      });
    },
    { threshold: 0.45 }
  );

  counters.forEach((el) => io.observe(el));
}

initCountUps();

const shot = new URLSearchParams(location.search).get("shot");
if (shot) {
  window.addEventListener("load", () => {
    document.getElementById(shot)?.scrollIntoView();
  });
}

function finishBoot(overlay, { hold = false } = {}) {
  if (!overlay) {
    document.body.classList.remove("is-booting");
    return;
  }

  overlay.setAttribute("aria-busy", "false");
  document.body.classList.remove("is-booting");

  if (hold || new URLSearchParams(location.search).has("holdboot")) return;

  try {
    sessionStorage.setItem("inbound_booted", "true");
  } catch {
    /* private mode / blocked storage */
  }

  overlay.classList.add("boot-complete");
  setTimeout(() => overlay.remove(), 400);
}

function initBootSequence() {
  const overlay = document.getElementById("boot-loader");
  if (!overlay) {
    document.body.classList.remove("is-booting");
    return;
  }

  const params = new URLSearchParams(location.search);
  const forceBoot = params.has("boot") || params.get("boot") === "1";
  const holdBoot = params.has("holdboot");

  // Default: skip boot for judge/investor scans. Opt-in theater with ?boot=1
  if (!forceBoot && !holdBoot && !shot) {
    overlay.remove();
    document.body.classList.remove("is-booting");
    return;
  }

  overlay.hidden = false;

  if (shot && !forceBoot) {
    overlay.remove();
    document.body.classList.remove("is-booting");
    return;
  }

  document.body.classList.add("is-booting");

  try {
    if (!holdBoot && !forceBoot && sessionStorage.getItem("inbound_booted")) {
      overlay.remove();
      document.body.classList.remove("is-booting");
      return;
    }
  } catch {
    /* continue boot if storage unavailable */
  }

  const bar = document.getElementById("boot-bar");
  const percentText = document.getElementById("boot-percentage");
  const statusText = document.getElementById("boot-status");
  const terminal = document.getElementById("boot-terminal");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let finished = false;
  let interval = null;

  const complete = (opts) => {
    if (finished) return;
    finished = true;
    if (interval) clearInterval(interval);
    finishBoot(overlay, opts);
  };

  // Tap / click / Escape skips first-visit boot (keeps sessionStorage via finishBoot).
  const skipBoot = (e) => {
    if (holdBoot) return;
    if (e) e.preventDefault();
    if (bar) bar.style.width = "100%";
    if (percentText) percentText.textContent = "100%";
    if (statusText) statusText.textContent = "RUNTIME ZERO-CLOUD ONLINE";
    complete();
  };
  overlay.addEventListener("click", skipBoot);
  overlay.addEventListener("touchend", skipBoot, { passive: false });
  const onKey = (e) => {
    if (e.key === "Escape" || e.key === "Enter" || e.key === " ") skipBoot(e);
  };
  document.addEventListener("keydown", onKey, { once: true });

  const skipHint = document.getElementById("boot-skip-hint");
  if (skipHint) skipHint.hidden = false;

  if (reduced) {
    if (bar) bar.style.width = "100%";
    if (percentText) percentText.textContent = "100%";
    if (statusText) statusText.textContent = "RUNTIME ZERO-CLOUD ONLINE";
    complete();
    return;
  }

  const logs = [
    { pct: 25, status: "INITIALIZING POSE PIPELINE...", log: "> YOLOv8-POSE: 17 SKELETAL KEYPOINTS LOCKED" },
    { pct: 58, status: "ALLOCATING DUAL-TIMER MEMORY...", log: "> CLOCKS: [BILLABLE_LABOR] + [PAYROLL_ATTENDANCE] ACTIVE" },
    { pct: 85, status: "CONFIGURING BOT WEBHOOK...", log: "> DISPATCH: TELEGRAM ENCRYPTED ENDPOINT READY" },
    { pct: 100, status: "RUNTIME ZERO-CLOUD ONLINE", log: "> SYSTEM VERIFIED: EDGE NODE AT MAXIMUM THROUGHPUT" },
  ];

  let current = 0;
  let logIdx = 0;

  interval = setInterval(() => {
    current += Math.floor(Math.random() * 8) + 4;

    if (logIdx < logs.length && current >= logs[logIdx].pct) {
      if (statusText) statusText.textContent = logs[logIdx].status;
      if (terminal) {
        const newLine = document.createElement("div");
        newLine.className = logIdx === logs.length - 1 ? "log-entry log-highlight" : "log-entry";
        newLine.textContent = logs[logIdx].log;
        terminal.appendChild(newLine);
      }
      logIdx += 1;
    }

    if (current >= 100) {
      current = 100;
      clearInterval(interval);
      interval = null;
      if (bar) bar.style.width = "100%";
      if (percentText) percentText.textContent = "100%";

      setTimeout(() => complete(), 250);
      return;
    }

    if (bar) bar.style.width = `${current}%`;
    if (percentText) percentText.textContent = `${current}%`;
  }, 45);
}

initBootSequence();

function initPpNodeMap() {
  const el = document.getElementById("pp-map");
  if (!el || typeof L === "undefined") return;

  const lat = 11.5564;
  const lng = 104.9282;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const map = L.map(el, {
    zoomControl: false,
    attributionControl: true,
    scrollWheelZoom: false,
    dragging: !window.matchMedia("(pointer: coarse)").matches,
  }).setView([lat, lng], 12);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener noreferrer">OSM</a> &copy; <a href="https://carto.com/attributions" rel="noopener noreferrer">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19,
  }).addTo(map);

  L.control.zoom({ position: "topright" }).addTo(map);

  const pinClass = reduced ? "pp-node-pin" : "pp-node-pin pp-node-pin--pulse";
  const icon = L.divIcon({
    className: "pp-node-marker",
    html: `<span class="${pinClass}" aria-hidden="true"><i></i></span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });

  L.marker([lat, lng], { icon, keyboard: false, title: "PP-NODE // PHNOM PENH" }).addTo(map);

  // Leaflet needs a invalidate after layout settles (grid / fonts).
  requestAnimationFrame(() => map.invalidateSize());
  window.addEventListener("load", () => map.invalidateSize(), { once: true });
}

initPpNodeMap();
