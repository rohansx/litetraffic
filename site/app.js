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
    document.querySelectorAll("[data-profile]").forEach((candidate) => candidate.setAttribute("aria-selected", String(candidate === button)));
    drawProfile(button.dataset.profile);
  });
});

document.querySelector("#copy-command").addEventListener("click", async (event) => {
  const command = "python -m pip install -e '.[dev]'";
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(command);
    button.textContent = "Copied";
    window.setTimeout(() => { button.textContent = "Copy"; }, 1600);
  } catch (_) {
    button.textContent = "Select command";
  }
});

drawProfile("spiky");
