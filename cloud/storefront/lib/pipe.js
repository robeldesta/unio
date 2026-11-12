import http from 'node:http';
import { currentHost } from './odoo';

export async function pipe(path) {
  const host = await currentHost();
  const ODOO = new URL(process.env.ODOO_URL || 'http://127.0.0.1:8569');
  return new Promise((resolve) => {
    const r = http.request({ hostname: ODOO.hostname, port: ODOO.port || 80, path, headers: { Host: host } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve(new Response(Buffer.concat(chunks), { status: res.statusCode,
        headers: { 'Content-Type': res.headers['content-type'] || 'application/octet-stream', 'Cache-Control': res.headers['cache-control'] || 'public, max-age=3600' } })));
    });
    r.on('error', () => resolve(new Response('', { status: 502 })));
    r.end();
  });
}
