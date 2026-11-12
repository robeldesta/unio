import { get, currentHost } from '@/lib/odoo';

export const dynamic = 'force-dynamic';

export default async function sitemap() {
  const host = await currentHost();
  const base = `https://${host}`;
  const m = (await get('/unio/site/api/sitemap', { ttl: 300 })) || { pages: [], products: [], categories: [] };
  return [
    ...m.pages.map((p) => ({ url: base + p, changeFrequency: 'weekly', priority: p === '/' ? 1 : 0.6 })),
    { url: `${base}/shop`, changeFrequency: 'daily', priority: 0.9 },
    ...(m.categories || []).map((c) => ({ url: `${base}/shop?cat=${c}`, changeFrequency: 'weekly', priority: 0.7 })),
    ...m.products.map((p) => ({ url: `${base}/product/${p.slug}`, lastModified: p.date, changeFrequency: 'weekly', priority: 0.8 })),
  ];
}
