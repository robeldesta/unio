// Talks to the company's Unio system. Every company has its own address (company.unio.et), and that address picks
// its database, so each call carries the visitor's host. Reads are cached for a few seconds per company.
import http from 'node:http';
import https from 'node:https';
import { headers } from 'next/headers';

const ODOO = new URL(process.env.ODOO_URL || 'http://127.0.0.1:8569');
const cache = new Map();

export async function currentHost() {
  const h = await headers();
  return (h.get('x-forwarded-host') || h.get('host') || '').split(',')[0].trim().toLowerCase();
}

export async function clientIp() {
  const h = await headers();
  return (h.get('x-forwarded-for') || h.get('x-real-ip') || '').split(',')[0].trim();
}

function request(host, path, { method = 'GET', body, ip } = {}) {
  const lib = ODOO.protocol === 'https:' ? https : http;
  const data = body ? JSON.stringify(body) : null;
  return new Promise((resolve, reject) => {
    const req = lib.request({
      hostname: ODOO.hostname, port: ODOO.port || (ODOO.protocol === 'https:' ? 443 : 80), path, method,
      headers: {
        Host: host, 'X-Forwarded-Host': host, 'X-Forwarded-Proto': 'https', Accept: 'application/json',
        ...(ip ? { 'X-Forwarded-For': ip } : {}),
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
      },
      timeout: 15000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch { /* not json: an error page */ }
        resolve({ status: res.statusCode, json });
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

/** GET with a short cache (seconds) per company and path. */
export async function get(path, { ttl = 15, host } = {}) {
  host = host || (await currentHost());
  const key = host + ' ' + path;
  const hit = cache.get(key);
  if (ttl && hit && hit.until > Date.now()) return hit.value;
  const value = (async () => {
    const r = await request(host, path);
    return r.status === 200 ? r.json : null;
  })();
  if (ttl) {
    if (cache.size > 5000) cache.clear();
    cache.set(key, { value, until: Date.now() + ttl * 1000 });
  }
  try {
    return await value;
  } catch (e) {
    cache.delete(key);
    return null;
  }
}

export async function post(path, body, { host, ip } = {}) {
  host = host || (await currentHost());
  const r = await request(host, path, { method: 'POST', body, ip });
  return { status: r.status, json: r.json || { ok: false, error: 'Something went wrong. Please try again.' } };
}

/** The shop's own address as the visitor sees it (for payment return links). */
export async function origin() {
  const host = await currentHost();
  const h = await headers();
  const proto = h.get('x-forwarded-proto') || (/localhost|^127\./.test(host) ? 'http' : 'https');
  return `${proto.split(',')[0]}://${host}`;
}

export const site = () => get('/unio/site/api/site', { ttl: 20 });
