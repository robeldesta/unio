import { notFound } from 'next/navigation';
import { get, site as getSite } from '@/lib/odoo';
import Blocks from '@/components/Blocks';
import Offline from '@/components/Offline';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ searchParams }) {
  const page = await get('/unio/site/api/page?slug=home');
  return { title: page?.seo?.title ? { absolute: page.seo.title } : undefined, description: page?.seo?.description || undefined };
}

export default async function Home({ searchParams }) {
  const sp = await searchParams;
  const s = await getSite();
  const preview = sp?.preview;
  if (!s.online && !preview) return <Offline site={s} />;
  const page = await get(`/unio/site/api/page?slug=home${preview ? `&preview=${encodeURIComponent(preview)}` : ''}`, { ttl: preview ? 0 : 15 });
  if (!page) notFound();
  return <Blocks blocks={page.blocks} site={s} />;
}
