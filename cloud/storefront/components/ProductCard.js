import Link from 'next/link';
import Icon from './Icon';
import { AddToCart } from './Cart';
import { money } from '@/lib/format';

export function StockBadge({ stock }) {
  if (!stock) return null;
  if (stock.state === 'out') return <span className="u-badge u-badge-out">Sold out</span>;
  if (stock.state === 'backorder') return <span className="u-badge">To order</span>;
  if (stock.state === 'low') return <span className="u-badge u-badge-low">{stock.left != null ? `Only ${stock.left} left` : 'Few left'}</span>;
  return null;
}

export default function ProductCard({ p, site }) {
  return (
    <Link href={`/product/${p.slug}`} className="u-card">
      <div className="u-card-img">
        {p.image ? <img src={p.image} alt={p.name} loading="lazy" /> : <Icon name="bag" size={42} />}
        <StockBadge stock={p.stock} />
      </div>
      <div className="u-card-body">
        <div className="u-card-name">{p.name}</div>
        {p.short && <div className="u-card-short">{p.short}</div>}
        <div className="u-card-foot">
          <span className="u-price">{money(p.price, site)}</span>
          <AddToCart product={p} site={site} />
        </div>
      </div>
    </Link>
  );
}

export function Grid({ products, site }) {
  return <div className="u-grid">{products.map((p) => <ProductCard key={p.id} p={p} site={site} />)}</div>;
}
