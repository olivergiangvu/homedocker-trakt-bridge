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
:root{color-scheme:dark;--bg:#0b0f14;--card:#121923;--muted:#8fa3b8;--line:#263241;--ok:#50c878;--warn:#e6b450;--bad:#ef6b73;--accent:#73a7ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#edf4fb;font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1120px;margin:40px auto;padding:0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:16px 0;box-shadow:0 12px 36px #0004}h1,h2,h3{margin:.2em 0 .7em}p{color:#d7e2ec}.muted{color:var(--muted)}code{background:#0a111a;border:1px solid var(--line);padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}.btn{display:inline-block;border:0;border-radius:9px;padding:10px 14px;background:var(--accent);color:#07111f;font-weight:700;text-decoration:none;cursor:pointer}.btn.secondary{background:#273548;color:#e8f0f8}.btn.bad{background:#d85b63;color:white}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.stat{background:#0d141d;border:1px solid var(--line);border-radius:12px;padding:14px}.stat .label{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.stat .value{display:block;margin-top:4px;font-size:18px;font-weight:750;overflow-wrap:anywhere}.status,.badge{font-weight:700}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:#0d141d}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}input{width:100%;background:#0a111a;color:#eef;border:1px solid var(--line);border-radius:9px;padding:10px;margin:8px 0 12px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid var(--line);vertical-align:top}.url{word-break:break-all}small{color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px}.filter{padding:6px 10px;border-radius:999px;border:1px solid var(--line);text-decoration:none;color:#d7e2ec;background:#0d141d}.filter.active{border-color:var(--accent);color:#fff}.section-note{margin-top:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.compact td,.compact th{padding:7px}.right{text-align:right}@media(max-width:640px){.wrap{margin:20px auto;padding:0 12px}.card{padding:16px}td,th{padding:7px 5px;font-size:13px}}
</style></head>
<body><div class="wrap">${body}</div></body></html>`;
}
