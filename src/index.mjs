import { loadConfig, APP_NAME, APP_VERSION } from './config.mjs';
import { BridgeDb } from './db.mjs';
import { ManagedTraktClient } from './managed-trakt.mjs';
import { createServer } from './server.mjs';

const config = loadConfig();
const db = new BridgeDb(config);
const trakt = new ManagedTraktClient(config, db);
const server = createServer({ config, db, trakt });

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

let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(JSON.stringify({ level: 'info', message: `received ${signal}, shutting down` }));
  server.close(() => {
    try { db.close(); } catch {}
    process.exit(0);
  });
  setTimeout(() => {
    try { db.close(); } catch {}
    process.exit(1);
  }, 10_000).unref();
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
