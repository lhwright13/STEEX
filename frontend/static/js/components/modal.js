// components/modal.js — one lightweight overlay modal, reused by clickable feed
// rows (today-events, event panel). Caller passes already-escaped innerHTML.
let overlay = null;

function ensure() {
  if (overlay) return overlay;
  overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.style.display = "none";
  overlay.innerHTML =
    '<div class="modal-card" role="dialog" aria-modal="true">' +
    '<button class="modal-close" aria-label="Close">&times;</button>' +
    '<div class="modal-body"></div></div>';
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.classList.contains("modal-close")) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
  document.body.appendChild(overlay);
  return overlay;
}

export function openModal(html) {
  const ov = ensure();
  ov.querySelector(".modal-body").innerHTML = html;
  ov.style.display = "flex";
}

export function close() {
  if (overlay) overlay.style.display = "none";
}
