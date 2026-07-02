(function () {
  const tauri = window.__TAURI__;
  if (!tauri) return;

  const EVENT_RUNS = "ringer-runs";
  const app = document.getElementById("app");
  const topbar = document.querySelector(".topbar");
  const headline = document.getElementById("headline");
  const subtitle = document.getElementById("subtitle");
  const clock = document.getElementById("clock");
  let collapsed = false;
  let latestRuns = [];

  document.documentElement.classList.add("tauri-hud");

  const style = document.createElement("style");
  style.textContent = `
    .tauri-hud, .tauri-hud body {
      height: 100%;
      min-height: 100%;
      overflow: hidden;
      background: transparent;
    }
    .tauri-hud body:before { display: none; }
    .tauri-hud .shell {
      width: 100%;
      height: 100%;
      padding: 0;
      overflow: hidden;
      border-radius: 14px;
      background:
        radial-gradient(circle at 50% -20%, rgba(40,215,255,.14), transparent 24rem),
        linear-gradient(180deg, rgba(8,10,15,.94), rgba(13,17,25,.97) 60%, rgba(8,10,15,.94));
      box-shadow: 0 18px 50px rgba(0,0,0,.38);
    }
    .tauri-hud .topbar {
      height: 34px;
      grid-template-columns: auto minmax(0, 1fr) auto auto;
      gap: 9px;
      padding: 0 8px 0 14px;
      border-bottom: 1px solid rgba(255,255,255,.10);
      background: rgba(5,8,12,.50);
      user-select: none;
      -webkit-user-select: none;
    }
    .tauri-hud .top-dot {
      width: 10px;
      height: 10px;
    }
    .tauri-hud .title {
      display: block;
      min-width: 0;
    }
    .tauri-hud h1 {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 34px;
      text-transform: none;
    }
    .tauri-hud .subtitle,
    .tauri-hud .clock {
      display: none;
    }
    .tauri-hud #app {
      height: calc(100% - 34px);
      gap: 10px;
      padding: 10px;
      overflow: auto;
    }
    .tauri-hud .tasks {
      grid-template-columns: repeat(auto-fill, minmax(106px, 1fr));
    }
    .hud-button {
      width: 24px;
      height: 24px;
      display: grid;
      place-items: center;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: rgba(238,244,255,.86);
      font: 700 15px/1 system-ui, sans-serif;
      cursor: default;
    }
    .hud-button:hover {
      background: rgba(255,255,255,.10);
      color: #fff;
    }
    .tauri-hud.is-collapsed .shell {
      border-radius: 12px;
    }
    .tauri-hud.is-collapsed #app,
    .tauri-hud.is-collapsed .hud-hide {
      display: none;
    }
    .tauri-hud.is-collapsed .topbar {
      border-bottom: 0;
    }
  `;
  document.head.appendChild(style);

  const currentWindow = tauri.window?.getCurrentWindow?.();
  const noDragSelector = "button, a, input, select, textarea, [data-no-drag]";

  // Swift-HUD parity: the whole background drags, while real controls stay
  // clickable even when nested inside draggable-looking chrome.
  document.addEventListener("mousedown", event => {
    if (event.button !== 0) return;
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    if (!target) return;
    if (target.closest(noDragSelector)) return;
    const drag = currentWindow?.startDragging?.();
    if (drag?.catch) drag.catch(() => {});
  });

  if (topbar) {
    const collapseButton = document.createElement("button");
    collapseButton.type = "button";
    collapseButton.className = "hud-button hud-collapse";
    collapseButton.title = "Collapse";
    collapseButton.textContent = "-";

    const hideButton = document.createElement("button");
    hideButton.type = "button";
    hideButton.className = "hud-button hud-hide";
    hideButton.title = "Hide";
    hideButton.textContent = "x";

    topbar.append(collapseButton, hideButton);

    collapseButton.addEventListener("click", async event => {
      event.stopPropagation();
      collapsed = await invoke("toggle_collapse");
      document.documentElement.classList.toggle("is-collapsed", collapsed);
      collapseButton.textContent = collapsed ? "+" : "-";
      collapseButton.title = collapsed ? "Expand" : "Collapse";
      renderHudTitle(latestRuns);
    });

    hideButton.addEventListener("click", event => {
      event.stopPropagation();
      invoke("hide_window");
    });
  }

  window.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      invoke("hide_window");
    }
  });

  listen(EVENT_RUNS, event => {
    latestRuns = Array.isArray(event.payload) ? event.payload : [];
    update(latestRuns);
    renderHudTitle(latestRuns);
  });

  function renderHudTitle(runs) {
    if (!headline) return;

    const liveRuns = runs.filter(run => run.state === "live");
    if (liveRuns.length > 0) {
      const agents = liveRuns.reduce((sum, run) => sum + (run.tasks || []).length, 0);
      headline.textContent = `${liveRuns.length} ringer${liveRuns.length === 1 ? "" : "s"} · ${agents} agent${agents === 1 ? "" : "s"}`;
    } else if (runs.length > 0) {
      const newest = runs[0];
      headline.textContent = newest.state === "died" ? `${newest.run_name || "ringer"} died` : (newest.run_name || "ringer");
    } else {
      headline.textContent = "no ringers running";
    }

    if (subtitle) subtitle.textContent = "";
    if (clock) clock.textContent = "";
  }

  function invoke(command) {
    return tauri.core.invoke(command);
  }

  function listen(eventName, handler) {
    return tauri.event.listen(eventName, handler);
  }
})();
