import http from 'node:http';

export async function startJsonServer(handler) {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString('utf8');
    let json = null;
    if (rawBody) {
      try { json = JSON.parse(rawBody); } catch { json = null; }
    }

    const url = new URL(req.url, 'http://fixture.local');
    const record = {
      method: req.method,
      path: url.pathname,
      search: url.search,
      headers: { ...req.headers },
      rawBody,
      json,
    };
    requests.push(record);

    try {
      const reply = await handler(record, requests) || { status: 404, body: { error: 'fixture_not_found' } };
      res.statusCode = reply.status ?? 200;
      for (const [name, value] of Object.entries(reply.headers || {})) res.setHeader(name, String(value));
      if (reply.body === undefined || reply.body === null) return res.end();
      if (typeof reply.body === 'string' || Buffer.isBuffer(reply.body)) return res.end(reply.body);
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      return res.end(JSON.stringify(reply.body));
    } catch (err) {
      res.statusCode = 500;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      return res.end(JSON.stringify({ error: 'fixture_error', message: err?.message || String(err) }));
    }
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });

  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  return {
    server,
    baseUrl,
    requests,
    close: () => new Promise((resolve, reject) => server.close((err) => err ? reject(err) : resolve())),
  };
}

export function redirectTraktFetch(baseUrl) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => {
    const source = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    if (source.hostname === 'api.trakt.tv' || source.hostname === 'auth.trakt.tv') {
      const target = new URL(`${source.pathname}${source.search}`, baseUrl);
      return originalFetch(target, init);
    }
    return originalFetch(input, init);
  };
  return () => { globalThis.fetch = originalFetch; };
}

export async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  return `http://127.0.0.1:${address.port}`;
}

export async function closeServer(server) {
  if (!server.listening) return;
  await new Promise((resolve, reject) => server.close((err) => err ? reject(err) : resolve()));
}
