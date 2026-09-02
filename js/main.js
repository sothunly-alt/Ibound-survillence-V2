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
const steps = [...document.querySelectorAll("[data-flow-step]")];
let demoSeconds = 4 * 3600 + 11 * 60 + 55;
let demoPhase = 0;

function setPhase(phase) {
  demoPhase = phase;
  steps.forEach((step) => step.classList.toggle("is-active", Number(step.dataset.flowStep) === phase));
  bbox?.classList.toggle("is-on", phase >= 1);
  phone?.classList.toggle("is-on", phase >= 2);
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

form?.addEventListener("submit", (e) => {
  e.preventDefault();
  form.hidden = true;
  success.hidden = false;
});

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const heroImg = document.querySelector(".hero-media img");
const finalImg = document.querySelector(".final-media img");
const navLinks = [...document.querySelectorAll(".nav-desktop a[href^='#']")];
const navSections = navLinks
  .map((link) => document.querySelector(link.getAttribute("href")))
  .filter(Boolean);

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
updateScroll();

function initScrollReveal() {
  if (reducedMotion) return;
  const nodes = document.querySelectorAll(
    ".section-head, .hud-frame, .flow-steps, .roi-card, .feature-card, .price-card, .partners, .testimonial, .final-content"
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

const bootLoader = document.getElementById("boot-loader");
const bootStatus = document.querySelector("[data-boot-status]");
const bootBar = document.querySelector("[data-boot-bar]");
const bootPct = document.querySelector("[data-boot-pct]");

function loadImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve();
    img.onerror = () => resolve();
    img.src = src;
  });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function setBootProgress(value) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  if (bootBar) bootBar.style.width = `${pct}%`;
  if (bootPct) bootPct.textContent = `${String(pct).padStart(2, "0")}%`;
}

async function runBootSequence() {
  if (!bootLoader) {
    document.body.classList.remove("is-booting");
    return;
  }

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const narrative = ["Calibrating optics", "Securing connection", "Detecting environment", "Bay locked"];
  let step = 0;
  if (bootStatus) bootStatus.textContent = narrative[0];

  const story = setInterval(() => {
    step = Math.min(step + 1, narrative.length - 1);
    if (bootStatus) bootStatus.textContent = narrative[step];
    setBootProgress(18 + step * 22);
  }, reduced ? 220 : 620);

  let progress = 8;
  const tick = setInterval(() => {
    progress = Math.min(progress + (reduced ? 18 : 4), 86);
    setBootProgress(progress);
  }, 120);

  const minHold = reduced ? 280 : 2200;
  const started = performance.now();

  await Promise.all([
    loadImage("assets/hero-garage.png"),
    loadImage("assets/demo-mechanic.png"),
    document.fonts ? document.fonts.ready.catch(() => undefined) : Promise.resolve(),
  ]);

  const elapsed = performance.now() - started;
  if (elapsed < minHold) await wait(minHold - elapsed);

  clearInterval(story);
  clearInterval(tick);
  if (bootStatus) bootStatus.textContent = narrative[narrative.length - 1];
  setBootProgress(100);

  await wait(reduced ? 40 : 220);

  if (new URLSearchParams(location.search).has("holdboot")) return;

  bootLoader.classList.add("is-done");
  bootLoader.setAttribute("aria-busy", "false");
  document.body.classList.remove("is-booting");

  let removed = false;
  const unmount = () => {
    if (removed) return;
    removed = true;
    bootLoader.remove();
  };
  bootLoader.addEventListener("transitionend", unmount, { once: true });
  setTimeout(unmount, 500);
}

if (shot) {
  bootLoader?.remove();
  document.body.classList.remove("is-booting");
} else {
  runBootSequence();
}
