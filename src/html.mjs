export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function page(title, body) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>
:root{color-scheme:dark;--bg:#0b0f14;--card:#121923;--muted:#8fa3b8;--line:#263241;--ok:#50c878;--warn:#e6b450;--bad:#ef6b73;--accent:#73a7ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#edf4fb;font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:820px;margin:48px auto;padding:0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:16px 0;box-shadow:0 12px 36px #0004}h1,h2{margin:.2em 0 .7em}p{color:#d7e2ec}.muted{color:var(--muted)}code{background:#0a111a;border:1px solid var(--line);padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}.btn{display:inline-block;border:0;border-radius:9px;padding:10px 14px;background:var(--accent);color:#07111f;font-weight:700;text-decoration:none;cursor:pointer}.btn.secondary{background:#273548;color:#e8f0f8}.btn.bad{background:#d85b63;color:white}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.status{font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}input{width:100%;background:#0a111a;color:#eef;border:1px solid var(--line);border-radius:9px;padding:10px;margin:8px 0 12px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid var(--line)}.url{word-break:break-all}small{color:var(--muted)}</style></head>
<body><div class="wrap">${body}</div></body></html>`;
}
