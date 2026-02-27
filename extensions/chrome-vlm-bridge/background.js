/**
 * Giva VLM Bridge — Background Service Worker
 *
 * State machine:
 *   idle → polling → navigating → capturing → analyzing → acting → (loop or complete)
 *
 * Polls the Giva API for VLM tasks. When a task is found, captures the visible
 * tab as a screenshot, sends it to the VLM for analysis, and dispatches the
 * resulting action (click, type, scroll, navigate) to the content script.
 */

const API_BASE = "http://127.0.0.1:7483/api/vlm";
const POLL_INTERVAL_MS = 3000;
const ACTION_DELAY_MS = 800;
const MAX_STEPS_PER_TASK = 50; // Safety limit to prevent infinite loops

let state = "idle";
let currentTask = null;
let stepCount = 0;
let pollTimer = null;

function log(level, ...args) {
  const ts = new Date().toISOString();
  const prefix = `[Giva VLM ${ts}] [${state}]`;
  if (level === "error") {
    console.error(prefix, ...args);
  } else if (level === "warn") {
    console.warn(prefix, ...args);
  } else {
    console.log(prefix, ...args);
  }
}

function setState(newState) {
  log("info", `State: ${state} → ${newState}`);
  state = newState;
}

// --- API Helpers ---

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (resp.status === 204) return null;
  if (!resp.ok) throw new Error(`API GET ${path}: ${resp.status}`);
  return resp.json();
}

async function apiPost(path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`API POST ${path}: ${resp.status}`);
  return resp.json();
}

// --- Main Loop ---

async function pollForTask() {
  if (state !== "idle" && state !== "polling") return;
  setState("polling");

  try {
    const task = await apiGet("/tasks/current");
    if (!task) {
      setState("idle");
      return;
    }

    log("info", `Task found: ${task.task_uuid.slice(0, 8)} — "${task.objective}"`);
    currentTask = task;
    stepCount = 0;

    // Navigate to target URL if not already there
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url !== task.target_url) {
      setState("navigating");
      log("info", `Navigating to: ${task.target_url}`);
      await chrome.tabs.update(tab.id, { url: task.target_url });
      // Wait for page load
      await waitForTabLoad(tab.id);
    }

    // Start the capture → analyze → act loop
    await executeLoop();
  } catch (err) {
    log("error", "Poll error:", err.message);
    setState("idle");
  }
}

function waitForTabLoad(tabId) {
  return new Promise((resolve) => {
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        // Extra delay for dynamic content
        setTimeout(resolve, ACTION_DELAY_MS);
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    // Timeout fallback
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 15000);
  });
}

async function executeLoop() {
  while (currentTask && stepCount < MAX_STEPS_PER_TASK) {
    stepCount++;
    log("info", `Step ${stepCount}/${MAX_STEPS_PER_TASK}`);

    // 1. Capture screenshot
    setState("capturing");
    let screenshot;
    try {
      screenshot = await chrome.tabs.captureVisibleTab(null, {
        format: "png",
      });
    } catch (err) {
      log("error", "Screenshot capture failed:", err.message);
      await completeTask(false, `Screenshot capture failed: ${err.message}`);
      return;
    }

    // Strip data URL prefix to get raw base64
    const b64 = screenshot.replace(/^data:image\/png;base64,/, "");

    // 2. Send to VLM for analysis
    setState("analyzing");
    let action;
    try {
      action = await apiPost("/vision/analyze", {
        task_uuid: currentTask.task_uuid,
        screenshot_b64: b64,
      });
    } catch (err) {
      log("error", "VLM analyze failed:", err.message);
      await completeTask(false, `VLM analysis failed: ${err.message}`);
      return;
    }

    log("info", `VLM action: ${action.action_type}`, action);

    // 3. Check for terminal actions
    if (action.action_type === "done") {
      await completeTask(true, action.reasoning || "Task completed successfully");
      return;
    }
    if (action.action_type === "fail") {
      await completeTask(false, action.reasoning || "VLM reported failure");
      return;
    }

    // 4. Execute the action via content script
    setState("acting");
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

      if (action.action_type === "navigate" && action.url) {
        log("info", `Navigating to: ${action.url}`);
        await chrome.tabs.update(tab.id, { url: action.url });
        await waitForTabLoad(tab.id);
      } else {
        // Send action to content script
        const result = await chrome.tabs.sendMessage(tab.id, {
          type: "vlm_action",
          action: action,
        });
        log("info", "Content script result:", result);
      }
    } catch (err) {
      log("error", "Action execution failed:", err.message);
      await completeTask(false, `Action failed: ${err.message}`);
      return;
    }

    // 5. Wait for DOM to settle before next capture
    await delay(ACTION_DELAY_MS);
  }

  if (stepCount >= MAX_STEPS_PER_TASK) {
    log("warn", "Max steps reached, completing task as failed");
    await completeTask(false, `Max steps (${MAX_STEPS_PER_TASK}) reached without completion`);
  }
}

async function completeTask(success, report) {
  log("info", `Completing task: success=${success}, report="${report.slice(0, 100)}"`);
  try {
    await apiPost("/tasks/complete", {
      task_uuid: currentTask.task_uuid,
      vlm_report: report,
      success: success,
    });
  } catch (err) {
    log("error", "Failed to report completion:", err.message);
  }
  currentTask = null;
  stepCount = 0;
  setState("idle");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- Lifecycle ---

function startPolling() {
  if (pollTimer) return;
  log("info", "Starting poll loop");
  pollTimer = setInterval(pollForTask, POLL_INTERVAL_MS);
  // Immediate first poll
  pollForTask();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    log("info", "Polling stopped");
  }
}

// Start polling when the service worker activates
startPolling();

// Re-start on install/update
chrome.runtime.onInstalled.addListener(() => {
  log("info", "Extension installed/updated");
  startPolling();
});
