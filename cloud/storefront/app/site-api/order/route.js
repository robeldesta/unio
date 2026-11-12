import { post, clientIp, origin } from '@/lib/odoo';

export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  const ip = await clientIp();
  const r = await post('/unio/site/api/order', { ...body, ip, origin: await origin() }, { ip });
  return Response.json(r.json, { status: r.status || 500 });
}
