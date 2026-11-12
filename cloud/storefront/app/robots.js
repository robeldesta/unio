import { currentHost } from '@/lib/odoo';

export const dynamic = 'force-dynamic';

export default async function robots() {
  const host = await currentHost();
  return { rules: [{ userAgent: '*', allow: '/', disallow: ['/checkout', '/order/', '/odoo', '/app', '/web'] }], sitemap: `https://${host}/sitemap.xml` };
}
