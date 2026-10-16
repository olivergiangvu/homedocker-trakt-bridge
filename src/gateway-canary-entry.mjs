/** Run only the HomeDocker-owned NextUp canary, never Trakt Bridge. */
import { launchCanaryGateway } from './gateway-standalone.mjs';

try {
  const { server, config } = launchCanaryGateway();
  server.on('listening', () => {
    console.log(JSON.stringify({
      component: 'isolated-nextup-canary',
      status: 'listening',
      port: config.jellyfinGatewayPort,
      mode: 'read_only_gateway_no_bridge_database_or_oauth',
    }));
  });
  server.on('error', (err) => {
    console.error(JSON.stringify({
      component: 'isolated-nextup-canary',
      status: 'failed',
      code: err?.code || 'listener_error',
    }));
    process.exit(1);
  });
  server.listen(config.jellyfinGatewayPort, '0.0.0.0');
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      if (server.listening) server.close(() => process.exit(0));
      else process.exit(0);
    });
  }
} catch {
  // No env values, profile IDs, paths, tokens or source data are logged.
  console.error(JSON.stringify({
    component: 'isolated-nextup-canary',
    status: 'invalid_configuration_or_proof',
  }));
  process.exit(1);
}
