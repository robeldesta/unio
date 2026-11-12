import Link from 'next/link';
import { get, site as getSite } from '@/lib/odoo';
import { Grid } from '@/components/ProductCard';
import Offline from '@/components/Offline';
import Sort from '@/components/Sort';
import Icon from '@/components/Icon';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ searchParams }) {
  const sp = await searchParams;
  const s = await getSite();
  const cat = s?.categories?.find((c) => c.slug === sp?.cat);
  return { title: cat ? cat.name : 'Shop' };
}

export default async function Shop({ searchParams }) {
  const sp = await searchParams;
  const s = await getSite();
  if (!s.online) return <Offline site={s} />;
  const qs = new URLSearchParams();
  for (const k of ['cat', 'q', 'sort', 'page']) if (sp?.[k]) qs.set(k, sp[k]);
  const r = (await get(`/unio/site/api/products?${qs}`)) || { products: [], total: 0, pages: 1, page: 1 };
  const cat = s.categories.find((c) => c.slug === sp?.cat);
  const link = (patch) => {
    const n = new URLSearchParams(qs);
    for (const [k, v] of Object.entries(patch)) (v ? n.set(k, v) : n.delete(k));
    if (!('page' in patch)) n.delete('page');
    const str = n.toString();
    return `/shop${str ? '?' + str : ''}`;
  };
  return (
    <>
      <div className="u-pagehead">
        <div className="u-wrap">
          <h1>{sp?.q ? `“${sp.q}”` : cat ? cat.name : 'Shop'}</h1>
          <p className="u-muted">{r.total} {r.total === 1 ? 'product' : 'products'}</p>
        </div>
      </div>
      <section className="u-section u-section-tight">
        <div className="u-wrap">
          <div className="u-filters">
            <div className="u-chips">
              <Link href={link({ cat: null })} className={!sp?.cat ? 'on' : ''}>All</Link>
              {s.categories.map((c) => <Link key={c.slug} href={link({ cat: c.slug })} className={sp?.cat === c.slug ? 'on' : ''}>{c.name}</Link>)}
            </div>
            <Sort value={sp?.sort || ''} />
          </div>
          {r.products.length ? <Grid products={r.products} site={s} /> : (
            <div className="u-empty"><Icon name="search" size={40} /><p>Nothing found.</p><Link className="u-btn" href="/shop">See all products</Link></div>
          )}
          {r.pages > 1 && (
            <nav className="u-pages">
              {Array.from({ length: r.pages }, (_, i) => i + 1).map((n) => (
                <Link key={n} href={link({ page: n === 1 ? null : String(n) })} className={n === r.page ? 'on' : ''}>{n}</Link>
              ))}
            </nav>
          )}
        </div>
      </section>
    </>
  );
}
