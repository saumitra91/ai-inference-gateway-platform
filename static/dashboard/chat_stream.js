function readSseDeltas(sseEventText) {
  const lines = sseEventText.split("\n");
  const out = [];
  for (const line of lines) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice("data:".length).trim();
    if (payload === "[DONE]") continue;
    try {
      const obj = JSON.parse(payload);
      const choice = obj?.choices?.[0];
      const delta = choice?.delta;
      const content = delta?.content;
      if (typeof content === "string" && content.length) out.push(content);
    } catch {
      // ignore partial frames
    }
  }
  return out.join("");
}

function getCookie(name) {
  const parts = (`; ${document.cookie}`).split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift() || "";
  return "";
}

async function* streamChat({ model, system, user, backend, signal }) {
  const body = {
    model,
    stream: true,
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
    ...(backend ? { backend } : {}),
  };

  const csrftoken = getCookie("csrftoken");
  const res = await fetch("/ui/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-CSRFToken": csrftoken || "",
    },
    credentials: "same-origin",
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    const errText = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${errText || res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const delta = readSseDeltas(part);
      if (delta) yield delta;
    }
  }

  if (buffer.trim().length) {
    const delta = readSseDeltas(buffer);
    if (delta) yield delta;
  }
}

function wire() {
  const out = document.getElementById("out");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");
  const model = document.getElementById("model");
  const backend = document.getElementById("backend");
  const system = document.getElementById("system");
  const user = document.getElementById("user");

  if (!out || !send || !stop || !model || !backend || !system || !user) return;

  let controller = null;

  send.addEventListener("click", async () => {
    out.textContent = "";
    send.disabled = true;
    stop.disabled = false;
    controller = new AbortController();

    try {
      for await (const delta of streamChat({
        model: model.value || "default",
        backend: backend.value,
        system: system.value,
        user: user.value,
        signal: controller.signal,
      })) {
        out.textContent += delta;
      }
    } catch (e) {
      out.textContent += `\n\n[error] ${e?.message || String(e)}`;
    } finally {
      send.disabled = false;
      stop.disabled = true;
      controller = null;
    }
  });

  stop.addEventListener("click", () => {
    controller?.abort();
  });
}

document.addEventListener("DOMContentLoaded", wire);
