/**
 * Giva VLM Bridge — Content Script
 *
 * Receives action messages from the background service worker and executes
 * DOM actions: click, type, scroll. Reports success/failure back.
 */

function log(...args) {
  console.log("[Giva VLM Content]", ...args);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== "vlm_action") return;

  const action = message.action;
  log(`Action received: ${action.action_type}`, action);

  try {
    let result;
    switch (action.action_type) {
      case "click":
        result = handleClick(action.coordinates);
        break;
      case "type":
        result = handleType(action.text_to_type);
        break;
      case "scroll":
        result = handleScroll(action.scroll_amount);
        break;
      default:
        result = { success: false, error: `Unknown action: ${action.action_type}` };
    }
    log("Action result:", result);
    sendResponse(result);
  } catch (err) {
    log("Action error:", err.message);
    sendResponse({ success: false, error: err.message });
  }

  // Return true to indicate async response
  return true;
});

function handleClick(coordinates) {
  if (!coordinates || coordinates.length < 2) {
    return { success: false, error: "Missing coordinates for click" };
  }

  const [x, y] = coordinates;

  // Validate coordinates are within viewport
  if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
    return {
      success: false,
      error: `Coordinates (${x}, ${y}) outside viewport (${window.innerWidth}x${window.innerHeight})`,
    };
  }

  const element = document.elementFromPoint(x, y);
  if (!element) {
    return { success: false, error: `No element at (${x}, ${y})` };
  }

  log(`Clicking element at (${x}, ${y}):`, element.tagName, element.className);

  // Dispatch mousedown, mouseup, click sequence for better compatibility
  const events = ["mousedown", "mouseup", "click"];
  for (const eventType of events) {
    const event = new MouseEvent(eventType, {
      bubbles: true,
      cancelable: true,
      clientX: x,
      clientY: y,
      view: window,
    });
    element.dispatchEvent(event);
  }

  return {
    success: true,
    element: `${element.tagName}.${element.className}`,
  };
}

function handleType(text) {
  if (!text) {
    return { success: false, error: "Missing text_to_type" };
  }

  const element = document.activeElement;
  if (!element || element === document.body) {
    return { success: false, error: "No focused element to type into" };
  }

  log(`Typing into:`, element.tagName, element.className);

  // For input/textarea, set value and dispatch events
  if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
    element.value = text;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  } else if (element.isContentEditable) {
    // For contenteditable elements
    element.textContent = text;
    element.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    return { success: false, error: `Element ${element.tagName} is not editable` };
  }

  return {
    success: true,
    element: `${element.tagName}.${element.className}`,
    text_length: text.length,
  };
}

function handleScroll(amount) {
  const scrollAmount = amount || 300; // Default 300px down
  log(`Scrolling by ${scrollAmount}px`);

  window.scrollBy({
    top: scrollAmount,
    behavior: "smooth",
  });

  return {
    success: true,
    scrolled_by: scrollAmount,
    new_scroll_y: window.scrollY + scrollAmount,
  };
}
