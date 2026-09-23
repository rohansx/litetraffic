const profiles = {
  spiky: {
    values: [2, 2, 2, 2, 7, 2, 2, 2, 2, 9, 2, 2, 2, 6, 2, 2, 2, 2],
    description: "A steady baseline interrupted by brief, high-volume spikes.",
    label: "Spiky traffic profile chart",
  },
  random: {
    values: [0, 0, 5, 7, 4, 0, 0, 0, 8, 5, 0, 0, 3, 7, 6, 0, 0, 0],
    description: "Mostly quiet, with seeded bursts that arrive as short traffic mountains.",
    label: "Random burst traffic profile chart",
  },
  sustained: {
    values: [1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 3, 1, 1, 1, 1, 1],
    description: "A controlled ramp into a plateau, followed by an immediate recovery drop.",
    label: "Sustained burst traffic profile chart",
  },
};

const chart = document.querySelector("#profile-chart");
const line = document.querySelector("#profile-line");
const area = document.querySelector("#profile-area");
const points = document.querySelector("#profile-points");
const description = document.querySelector(".profile-description");
const themeButton = document.querySelector("#theme-toggle");
const systemTheme = matchMedia("(prefers-color-scheme: dark)");

function updateThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  themeButton.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
  themeButton.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
  themeButton.setAttribute("aria-pressed", String(dark));
  document.querySelector('meta[name="theme-color"]').content = dark ? "#121b17" : "#f3f5f1";
}

themeButton.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("litetraffic-theme", next); } catch (_) { /* Preference remains active for this visit. */ }
  updateThemeButton();
});

systemTheme.addEventListener("change", () => {
  try { if (localStorage.getItem("litetraffic-theme")) return; } catch (_) { /* Follow the system preference. */ }
  document.documentElement.dataset.theme = systemTheme.matches ? "dark" : "light";
  updateThemeButton();
});

function drawProfile(name) {
  const profile = profiles[name];
  const width = 720;
  const bottom = 275;
  const top = 22;
  const max = 10;
  const coords = profile.values.map((value, index) => {
    const x = index * width / (profile.values.length - 1);
    const y = bottom - value / max * (bottom - top);
    return [x, y];
  });
  const path = coords.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  line.setAttribute("d", path);
  area.setAttribute("d", `${path} L${width} ${bottom} L0 ${bottom} Z`);
  points.replaceChildren(...coords.filter((_, index) => index % 4 === 0).map(([x, y]) => {
    const point = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    point.setAttribute("cx", x);
    point.setAttribute("cy", y);
    point.setAttribute("r", "4");
    return point;
  }));
  chart.setAttribute("aria-label", profile.label);
  description.textContent = profile.description;
}

document.querySelectorAll("[data-profile]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-profile]").forEach((candidate) => candidate.setAttribute("aria-pressed", String(candidate === button)));
    drawProfile(button.dataset.profile);
  });
});

const dashTabs = [...document.querySelectorAll('.dash-tabs [role="tab"]')];

function selectDashTab(tab) {
  dashTabs.forEach((candidate) => {
    const selected = candidate === tab;
    candidate.setAttribute("aria-selected", String(selected));
    candidate.tabIndex = selected ? 0 : -1;
    document.getElementById(candidate.getAttribute("aria-controls")).hidden = !selected;
  });
}

dashTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectDashTab(tab));
  tab.addEventListener("keydown", (event) => {
    const moves = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: dashTabs.length - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = dashTabs[(moves[event.key] + dashTabs.length) % dashTabs.length];
    selectDashTab(next);
    next.focus();
  });
});

document.querySelector("#copy-command").addEventListener("click", async (event) => {
  const command = "git clone https://github.com/rohansx/litetraffic.git\ncd litetraffic\npython3 -m venv .venv && . .venv/bin/activate\npython -m pip install -e .";
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(command);
    button.textContent = "Copied";
    window.setTimeout(() => { button.textContent = "Copy install commands"; }, 1600);
  } catch (_) {
    button.textContent = "Select command";
  }
});

const kitCaptions = {
  pass: "The included tenant example server, run as shipped. The rejected cross-tenant probes are expected statuses, so none count as unexpected failures.",
  fail: "The same server started with --wrong-silent-write answers each cross-tenant PUT with 403 but applies it anyway. Every status check passes; only the read-back compare catches it.",
};

document.querySelectorAll("[data-kit]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-kit]").forEach((candidate) => candidate.setAttribute("aria-pressed", String(candidate === button)));
    document.querySelectorAll("[data-kit-output]").forEach((output) => { output.hidden = output.dataset.kitOutput !== button.dataset.kit; });
    document.querySelector(".kit-caption").textContent = kitCaptions[button.dataset.kit];
  });
});

drawProfile("spiky");
updateThemeButton();
