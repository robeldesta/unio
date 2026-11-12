import Link from 'next/link';
import { notFound } from 'next/navigation';
import { get, site as getSite, currentHost } from '@/lib/odoo';
import { AddToCart } from '@/components/Cart';
import { Grid, StockBadge } from '@/components/ProductCard';
import Gallery from '@/components/Gallery';
import Icon from '@/components/Icon';
import Offline from '@/components/Offline';
import { money, waHref } from '@/lib/format';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }) {
  const { slug } = await params;
  const p = await get(`/unio/site/api/product?slug=${encodeURIComponent(slug)}`);
  if (!p) return {};
  const host = await currentHost();
  return { title: p.name, description: p.short || undefined,
    openGraph: { title: p.name, description: p.short || undefined, images: p.image ? [`https://${host}${p.image}`] : undefined } };
}

export default async function Product({ params }) {
  const { slug } = await params;
  const s = await getSite();
  if (!s.online) return <Offline site={s} />;
  const p = await get(`/unio/site/api/product?slug=${encodeURIComponent(slug)}`);
  if (!p) notFound();
  const host = await currentHost();
  const ld = {
    '@context': 'https://schema.org', '@type': 'Product', name: p.name, description: p.short || undefined,
    image: p.images.map((u) => `https://${host}${u}`), sku: p.code || undefined,
    offers: { '@type': 'Offer', price: p.price, priceCurrency: s.currency,
      availability: p.stock?.state === 'out' ? 'https://schema.org/OutOfStock' : 'https://schema.org/InStock' },
  };
  return (
    <>
      <section className="u-section u-section-tight">
        <div className="u-wrap">
          <nav className="u-crumbs"><Link href="/shop">Shop</Link>{p.category && <> / <Link href={`/shop?cat=${p.category.slug}`}>{p.category.name}</Link></>}</nav>
          <div className="u-product">
            <Gallery images={p.images} name={p.name} />
            <div className="u-product-info">
              <h1>{p.name}</h1>
              <div className="u-product-price">{money(p.price, s)} <small className="u-muted">incl. VAT{p.uom && p.uom !== 'Units' ? ` · per ${p.uom}` : ''}</small></div>
              <div className="u-product-stock">
                {p.stock?.state === 'out' ? <span className="u-out">Sold out</span>
                  : p.stock?.state === 'low' ? <StockBadge stock={p.stock} />
                  : p.stock?.state === 'backorder' ? <span>Made to order</span>
                  : <span className="u-in"><Icon name="check" size={16} /> In stock</span>}
              </div>
              {p.short && <p className="u-lead">{p.short}</p>}
              <AddToCart product={p} site={s} big />
              {s.contact?.whatsapp && (
                <a className="u-btn u-btn-ghost u-btn-block" href={waHref(s.contact.whatsapp, `Hello, I would like to order: ${p.name}`)} target="_blank" rel="noopener">Ask or order on WhatsApp</a>
              )}
              <ul className="u-perks">
                {s.delivery?.delivery && <li><Icon name="truck" size={20} /><span><b>Delivery</b>{s.delivery.text || `Fee ${money(s.delivery.fee, s)}`}{s.delivery.free_over > 0 ? `. Free over ${money(s.delivery.free_over, s)}.` : ''}</span></li>}
                {s.delivery?.pickup && <li><Icon name="store" size={20} /><span><b>Pickup</b>{s.delivery.pickup_text}</span></li>}
                {s.payment?.cod && <li><Icon name="cash" size={20} /><span><b>Pay on delivery</b>Cash or telebirr when it arrives.</span></li>}
              </ul>
              {p.description && <div className="u-prose u-desc" dangerouslySetInnerHTML={{ __html: p.description }} />}
            </div>
          </div>
        </div>
      </section>
      {p.related?.length > 0 && (
        <section className="u-section"><div className="u-wrap">
          <div className="u-section-head"><h2>You may also like</h2></div>
          <Grid products={p.related} site={s} />
        </div></section>
      )}
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />
    </>
  );
}
