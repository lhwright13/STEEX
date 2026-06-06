// core/dom.js — DOM helpers + HTML escaping (fixes the raw-innerHTML XSS/escaping
// gap, P-11). Every widget renders user/feed text through escapeHtml.
export const q = (sel, root = document) => root.querySelector(sel);
export const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ENT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ENT[c]);
}

// Safe attribute value (e.g. inside href="...") — escapes quotes too.
export const attr = escapeHtml;

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}
