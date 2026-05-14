function getCookie(name) {
  const parts = (`; ${document.cookie}`).split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift() || "";
  return "";
}

function addMessage(role, content, citations) {
  const container = document.getElementById("rag-conversation");
  if (!container) return;

  const el = document.createElement("div");
  el.className = `rag-message rag-message-${role}`;

  const roleLabel = document.createElement("div");
  roleLabel.className = "rag-role";
  roleLabel.textContent = role === "user" ? "You" : "Assistant";
  el.appendChild(roleLabel);

  const body = document.createElement("div");
  body.className = "rag-body";
  body.textContent = content;
  el.appendChild(body);

  if (citations && citations.length > 0) {
    const citeGroup = document.createElement("div");
    citeGroup.className = "rag-citations";
    citeGroup.innerHTML = "<strong>Sources:</strong>";
    for (const c of citations) {
      const badge = document.createElement("span");
      badge.className = "rag-citation-badge";
      badge.title = `Relevance: ${(c.relevance_score * 100).toFixed(0)}%`;
      badge.textContent = `${c.document_id.slice(0, 8)}… p.${c.page}`;
      citeGroup.appendChild(badge);
    }
    el.appendChild(citeGroup);
  }

  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function addStatus(text) {
  const status = document.getElementById("rag-status");
  if (!status) return;
  status.textContent = text;
  status.className = "rag-status";
}

function clearStatus() {
  const status = document.getElementById("rag-status");
  if (status) {
    status.textContent = "";
    status.className = "rag-status";
  }
}

function readSseDeltas(sseEventText, ragMetadata) {
  const lines = sseEventText.split("\n");
  const out = [];
  for (const line of lines) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice("data:".length).trim();
    if (payload === "[DONE]") continue;
    try {
      const obj = JSON.parse(payload);

      // RAG metadata event
      if (obj.type === "rag_metadata") {
        ragMetadata.found = obj.found;
        ragMetadata.citations = obj.citations || [];
        ragMetadata.chunksRetrieved = obj.chunks_retrieved || 0;
        continue;
      }

      // Standard OpenAI chunk
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

async function* streamRagChat({ message, documentIds, signal }) {
  const body = {
    model: "default",
    stream: true,
    max_tokens: 512,
    temperature: 0.7,
    messages: [
      { role: "user", content: message },
    ],
  };
  if (documentIds && documentIds.length > 0) {
    body.document_ids = documentIds;
  }

  const csrftoken = getCookie("csrftoken");
  const res = await fetch("/rag/api/completions", {
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

  const ragMeta = { found: false, citations: [], chunksRetrieved: 0 };

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
      const delta = readSseDeltas(part, ragMeta);
      if (delta) yield { type: "token", content: delta, ragMeta: { ...ragMeta } };
    }
  }

  if (buffer.trim().length) {
    const delta = readSseDeltas(buffer, ragMeta);
    if (delta) yield { type: "token", content: delta, ragMeta: { ...ragMeta } };
  }

  yield { type: "done", ragMeta: { ...ragMeta } };
}

function getSelectedDocumentIds() {
  const cbs = document.querySelectorAll(".rag-doc-cb:checked");
  return Array.from(cbs).map(cb => cb.value);
}

function toggleAllDocs(el) {
  const cbs = document.querySelectorAll(".rag-doc-cb");
  for (const cb of cbs) {
    cb.checked = el.checked;
  }
}

function wire() {
  const send = document.getElementById("rag-send");
  const stop = document.getElementById("rag-stop");
  const userInput = document.getElementById("rag-user");

  if (!send || !stop || !userInput) return;

  let controller = null;

  send.addEventListener("click", async () => {
    const message = userInput.value.trim();
    if (!message) return;

    userInput.value = "";
    send.disabled = true;
    stop.disabled = false;
    controller = new AbortController();

    const docIds = getSelectedDocumentIds();
    const allChecked = document.getElementById("doc-all")?.checked;
    const documentIds = allChecked ? null : docIds;

    addMessage("user", message, null);
    addStatus("Retrieving context...");

    let fullResponse = "";
    let finalCitations = [];
    let foundDocs = false;

    try {
      for await (const event of streamRagChat({
        message,
        documentIds: documentIds,
        signal: controller.signal,
      })) {
        if (event.type === "token") {
          if (!foundDocs && event.ragMeta.found) {
            clearStatus();
            foundDocs = true;
            const msg = event.ragMeta.chunksRetrieved
              ? `Retrieved ${event.ragMeta.chunksRetrieved} chunks`
              : "";
            if (msg) addStatus(msg);
          }
          fullResponse += event.content;
          if (foundDocs) {
            // Update the last assistant message if it exists, otherwise add one
            const container = document.getElementById("rag-conversation");
            let lastMsg = container?.lastElementChild;
            if (!lastMsg || !lastMsg.classList.contains("rag-message-assistant")) {
              addMessage("assistant", fullResponse, null);
            } else {
              const body = lastMsg.querySelector(".rag-body");
              if (body) body.textContent = fullResponse;
            }
          }
        }
        if (event.type === "done") {
          finalCitations = event.ragMeta.citations || [];
          if (!event.ragMeta.found) {
            clearStatus();
            addMessage("assistant", "I could not find this information in the uploaded documents.", null);
          }
          // Attach citations to the assistant message
          if (finalCitations.length > 0) {
            const container = document.getElementById("rag-conversation");
            const lastMsg = container?.lastElementChild;
            if (lastMsg && lastMsg.classList.contains("rag-message-assistant")) {
              // Remove existing citation group if present
              const oldCite = lastMsg.querySelector(".rag-citations");
              if (oldCite) oldCite.remove();
              const citeGroup = document.createElement("div");
              citeGroup.className = "rag-citations";
              citeGroup.innerHTML = "<strong>Sources:</strong>";
              for (const c of finalCitations) {
                const badge = document.createElement("span");
                badge.className = "rag-citation-badge";
                badge.title = `Relevance: ${(c.relevance_score * 100).toFixed(0)}%`;
                badge.textContent = `${c.document_id.slice(0, 8)}… p.${c.page}`;
                citeGroup.appendChild(badge);
              }
              lastMsg.appendChild(citeGroup);
            }
          }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        addMessage("assistant", `[error] ${e?.message || String(e)}`, null);
      }
      clearStatus();
    } finally {
      send.disabled = false;
      stop.disabled = true;
      controller = null;
      clearStatus();
    }
  });

  stop.addEventListener("click", () => {
    controller?.abort();
  });

  userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send.click();
    }
  });
}

document.addEventListener("DOMContentLoaded", wire);
