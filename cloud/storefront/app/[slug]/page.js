import { notFound } from 'next/navigation';
import { get, site as getSite } from '@/lib/odoo';
import Blocks from '@/components/Blocks';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }) {
  const { slug } = await params;
  const page = await get(`/unio/site/api/page?slug=${encodeURIComponent(slug)}`);
  if (!page) return {};
  return { title: page.seo?.title || page.name, description: page.seo?.description || undefined };
}

export default async function Page({ params, searchParams }) {
  const { slug } = await params;
  const sp = await searchParams;
  const s = await getSite();
  const preview = sp?.preview;
  if (!s.online && !preview) notFound();
  const page = await get(`/unio/site/api/page?slug=${encodeURIComponent(slug)}${preview ? `&preview=${encodeURIComponent(preview)}` : ''}`, { ttl: preview ? 0 : 15 });
  if (!page) notFound();
  return (
    <>
      {page.blocks?.[0]?.type !== 'hero' && <div className="u-pagehead"><div className="u-wrap"><h1>{page.name}</h1></div></div>}
      <Blocks blocks={page.blocks} site={s} />
    </>
  );
}
