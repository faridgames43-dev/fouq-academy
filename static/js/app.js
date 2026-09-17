// Small, dependency-free helpers shared across pages.

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

  // Live preview for any photo <input type="file"> paired with [data-preview-for]
  document.querySelectorAll("input[type=file][data-preview]").forEach((input) => {
    const target = document.querySelector(input.dataset.preview);
    if (!target) return;
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => { target.innerHTML = `<img src="${e.target.result}" alt="">`; };
      reader.readAsDataURL(file);
    });
  });

  // Gentle count-up animation for KPI numbers on first paint
  document.querySelectorAll(".kpi-value[data-count]").forEach((el) => animateCounter(el));
});

/** Animate a numeric element from 0 to its data-count/text value. */
function animateCounter(el, duration = 700) {
  const raw = el.dataset.count || el.textContent;
  const match = String(raw).match(/-?[\d.]+/);
  if (!match) return;
  const target = parseFloat(match[0]);
  const suffix = String(raw).slice(match.index + match[0].length);
  const start = performance.now();
  function tick(now) {
    const p = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    const val = target * eased;
    el.textContent = (Number.isInteger(target) ? Math.round(val) : val.toFixed(1)) + suffix;
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/** Small celebratory confetti burst — used on scan success / achievements. */
function confettiBurst(count = 26) {
  const colors = ["#cf931e", "#e8b94f", "#29b874", "#6acbe5", "#e5484d", "#6666ff"];
  const layer = document.createElement("div");
  layer.className = "confetti-layer";
  document.body.appendChild(layer);
  for (let i = 0; i < count; i++) {
    const piece = document.createElement("span");
    piece.className = "confetti-piece";
    piece.style.left = Math.random() * 100 + "vw";
    piece.style.background = colors[i % colors.length];
    piece.style.animationDelay = (Math.random() * 0.3) + "s";
    piece.style.transform = `rotate(${Math.random() * 360}deg)`;
    layer.appendChild(piece);
  }
  setTimeout(() => layer.remove(), 2000);
}
