import Link from 'next/link';
import Icon, { Social } from './Icon';
import { telHref, tgHref, waHref } from '@/lib/format';

export default function Footer({ site }) {
  const c = site.contact || {};
  const socials = [['facebook', c.facebook], ['instagram', c.instagram], ['tiktok', c.tiktok], ['telegram', c.telegram && tgHref(c.telegram)], ['whatsapp', c.whatsapp && waHref(c.whatsapp)]].filter((x) => x[1]);
  return (
    <footer className="u-footer">
      <div className="u-wrap u-footer-grid">
        <div>
          <div className="u-footer-name">{site.name}</div>
          {site.tagline && <p>{site.tagline}</p>}
          {socials.length > 0 && (
            <div className="u-socials">
              {socials.map(([n, url]) => <a key={n} href={url.startsWith('http') ? url : `https://${url}`} target="_blank" rel="noopener" aria-label={n}><Social name={n} /></a>)}
            </div>
          )}
        </div>
        <div>
          <h4>Visit or call</h4>
          {c.address && <p className="u-ic"><Icon name="pin" size={18} /><span>{c.address}</span></p>}
          {c.hours && <p className="u-ic"><Icon name="clock" size={18} /><span>{c.hours}</span></p>}
          {c.phone && <p className="u-ic"><Icon name="phone" size={18} /><a href={telHref(c.phone)}>{c.phone}</a></p>}
          {c.email && <p className="u-ic"><Icon name="mail" size={18} /><a href={`mailto:${c.email}`}>{c.email}</a></p>}
        </div>
        <div>
          <h4>Shop</h4>
          {site.shop && <p><Link href="/shop">All products</Link></p>}
          {site.categories.slice(0, 5).map((x) => <p key={x.slug}><Link href={`/shop?cat=${x.slug}`}>{x.name}</Link></p>)}
        </div>
        <div>
          <h4>Information</h4>
          {site.menu.map((m) => <p key={m.path}><Link href={m.path}>{m.name}</Link></p>)}
          {site.delivery?.text && <p className="u-muted-on-dark">{site.delivery.text}</p>}
        </div>
      </div>
      <div className="u-wrap u-footer-bottom">
        <span>© {new Date().getFullYear()} {site.company?.name || site.name}{site.company?.vat ? ` · TIN ${site.company.vat}` : ''}</span>
        <a href="https://unio.et" target="_blank" rel="noopener">Runs on Unio</a>
      </div>
    </footer>
  );
}
