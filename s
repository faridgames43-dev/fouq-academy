tatic/js/app.js// Small, dependency-free helpers shared across pages.

async function postJSON(url, data) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
  let body;
  try { body = await res.json(); } catch (e) { body = { ok: false, message: "خطأ غير متوقع" }; }
  if (!res.ok && !body.message) body.message = "حدث خطأ";
  body.ok = res.ok && body.ok !== false;
  return body;
}

function toast(el, message, ok) {
  el.textContent = message;
  el.className = "scan-feedback " + (ok ? "ok" : "err");
  el.style.display = "block";
}

document.addEventListener("DOMContentLoaded", () => {
  // Auto-focus barcode/QR scan input if present
  const scanInput = document.getElementById("scan-input");
  if (scanInput) {
    scanInput.focus();
    document.addEventListener("click", (e) => {
      if (!e.target.closest("button") && !e.target.closest("a")) scanInput.focus();
    });
  }
});
