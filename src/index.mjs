import { loadConfig, APP_NAME, APP_VERSION } from './config.mjs';
import { BridgeDb } from './db.mjs';
import { ManagedTraktClient } from './managed-trakt.mjs';
import { createServer } from './server.mjs';
import { createJellyfinGateway } from './jellyfin-gateway.mjs';
import { startAioReconciler } from './aio-reconciler.mjs';
import { startAioHistoryEvidenceJournal } from './aio-history-evidence.mjs';

const config = loadConfig();
const db = new BridgeDb(config);
const trakt = new ManagedTraktClient(config, db);
const server = createServer({ config, db, trakt });
// Gateway is a separate optional listener inside our Bridge process.
// Existing /health and Trakt endpoints remain untouched by default.
const gateway = config.jellyfinGatewayEnabled
  ? createJellyfinGateway({ config, db }) : null;
const aioReconciler = startAioReconciler({ config, db, trakt });
const aioHistoryEvidence = startAioHistoryEvidenceJournal({ config, db });

server.listen(config.port, '0.0.0.0', () => {
  console.log(JSON.stringify({
    level: 'info',
    app: APP_NAME,
    version: APP_VERSION,
    port: config.port,
    publicBaseUrl: config.publicBaseUrl,
    setupUrl: `${config.publicBaseUrl}/setup?key=<ADMIN_KEY>`,
    schemaVersion: db.schemaVersion(),
    migration: db.migration,
  }));
});

if (gateway) {
  gateway.on('error', (err) => {
    console.error(JSON.stringify({
      level: 'error', component: 'jellyfin-gateway', code: err?.code || 'listen_failed',
    }));
    // An explicitly enabled canary MUST NOT silently start without its
    // protected listener; Compose should observe the failure and roll back.
    process.exit(1);
  });
  gateway.listen(config.jellyfinGatewayPort, '0.0.0.0', () => {
    console.log(JSON.stringify({
      level: 'info', component: 'jellyfin-gateway', status: 'opt_in',
      port: config.jellyfinGatewayPort, route: '/Shows/NextUp',
    }));
  });
}

let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(JSON.stringify({ level: 'info', message: `received ${signal}, shutting down` }));
  if (gateway?.listening) gateway.close();
  server.close(() => {
    try { aioReconciler.stop(); } catch {}
    try { aioHistoryEvidence.stop(); } catch {}
    try { db.close(); } catch {}
    process.exit(0);
  });
  setTimeout(() => {
    try { aioReconciler.stop(); } catch {}
    try { aioHistoryEvidence.stop(); } catch {}
    try { db.close(); } catch {}
    process.exit(1);
  }, 10_000).unref();
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
