function getCookie(name) {
  const parts = (`; ${document.cookie}`).split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift() || "";
  return "";
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function statusBadge(status) {
  const colors = {
    uploaded: "warn",
    processing: "warn",
    ready: "ok",
    failed: "",
  };
  return `<span class="pill ${colors[status] || ""}">${escapeHtml(status)}</span>`;
}

async function loadDocuments() {
  const container = document.getElementById("rag-doc-list");
  if (!container) return;

  try {
    const res = await fetch("/rag/api/documents/", {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const docs = data.documents || [];

    if (docs.length === 0) {
      container.innerHTML = '<p class="muted">No documents uploaded yet.</p>';
      return;
    }

    let html = '<table class="rag-doc-table"><thead><tr>' +
      '<th>Filename</th><th>Size</th><th>Pages</th><th>Chunks</th><th>Status</th><th>Uploaded</th><th></th>' +
      '</tr></thead><tbody>';

    for (const d of docs) {
      const date = new Date(d.created_at).toLocaleString();
      html += `<tr>
        <td>${escapeHtml(d.original_filename)}</td>
        <td>${formatFileSize(d.file_size_bytes)}</td>
        <td>${d.page_count ?? "—"}</td>
        <td>${d.chunk_count ?? "—"}</td>
        <td>${statusBadge(d.status)}</td>
        <td class="muted small">${date}</td>
        <td>
          ${d.status === "failed" ? `<span class="muted small">${escapeHtml(d.error_message || "")}</span>` : ""}
          <button class="btn small" onclick="deleteDoc('${d.id}')">Delete</button>
        </td>
      </tr>`;
    }

    html += "</tbody></table>";
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<p class="muted">Failed to load documents: ${escapeHtml(e.message)}</p>`;
  }
}

async function deleteDoc(id) {
  if (!confirm("Delete this document and its indexed chunks?")) return;
  try {
    const res = await fetch(`/rag/api/documents/${id}/delete`, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCookie("csrftoken") || "",
      },
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadDocuments();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

async function uploadDocument(file) {
  const statusEl = document.getElementById("rag-upload-status");
  const btn = document.getElementById("rag-upload-btn");
  if (!statusEl || !btn) return;

  statusEl.textContent = "Uploading...";
  btn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/rag/api/documents/upload", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCookie("csrftoken") || "",
      },
      credentials: "same-origin",
      body: formData,
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

    statusEl.textContent = "Uploaded! Indexing in background...";
    statusEl.className = "rag-upload-status ok";

    // Poll for status changes
    pollStatus(data.id);
  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
    statusEl.className = "rag-upload-status error";
    btn.disabled = false;
  }
}

async function pollStatus(docId) {
  const statusEl = document.getElementById("rag-upload-status");
  const btn = document.getElementById("rag-upload-btn");
  const input = document.getElementById("rag-file-input");

  let attempts = 0;
  const maxAttempts = 120; // 2 minutes at 1s intervals

  const poll = async () => {
    if (attempts >= maxAttempts) {
      statusEl.textContent = "Timed out waiting for indexing. Check document list.";
      btn.disabled = false;
      await loadDocuments();
      return;
    }
    attempts++;

    try {
      const res = await fetch(`/rag/api/documents/${docId}/status`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (data.status === "ready") {
        statusEl.textContent = `Ready — ${data.chunk_count} chunks indexed.`;
        statusEl.className = "rag-upload-status ok";
        btn.disabled = false;
        if (input) input.value = "";
        await loadDocuments();
        return;
      }

      if (data.status === "failed") {
        statusEl.textContent = `Failed: ${data.error_message || "unknown error"}`;
        statusEl.className = "rag-upload-status error";
        btn.disabled = false;
        await loadDocuments();
        return;
      }

      // Still processing — poll again
      statusEl.textContent = `Indexing... (${data.page_count || "?"} pages extracted)`;
      setTimeout(poll, 1000);
    } catch (e) {
      statusEl.textContent = `Status check failed: ${e.message}`;
      btn.disabled = false;
    }
  };

  setTimeout(poll, 1000);
}

function wire() {
  const btn = document.getElementById("rag-upload-btn");
  const input = document.getElementById("rag-file-input");
  const statusEl = document.getElementById("rag-upload-status");

  if (btn && input) {
    btn.addEventListener("click", () => {
      const file = input?.files?.[0];
      if (!file) {
        if (statusEl) statusEl.textContent = "Select a PDF file first.";
        return;
      }
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        if (statusEl) statusEl.textContent = "Only PDF files are supported.";
        return;
      }
      uploadDocument(file);
    });
  }

  loadDocuments();
}

document.addEventListener("DOMContentLoaded", wire);
