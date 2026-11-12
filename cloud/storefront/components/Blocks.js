import Link from 'next/link';
import Icon from './Icon';
import { Grid } from './ProductCard';
import ContactForm from './ContactForm';
import { telHref, waHref } from '@/lib/format';

function Btn({ text, link, ghost }) {
  if (!text) return null;
  const href = link || '/shop';
  const cls = `u-btn u-btn-lg ${ghost ? 'u-btn-ghost' : ''}`;
  return href.startsWith('http') ? <a className={cls} href={href} target="_blank" rel="noopener">{text}</a> : <Link className={cls} href={href}>{text}</Link>;
}

function Hero({ b, site }) {
  const style = b.style || 'split';
  if (style === 'full' && b.image) {
    return (
      <section className="u-hero u-hero-full" style={{ backgroundImage: `url(${b.image})` }}>
        <div className="u-wrap"><div className="u-hero-copy">
          <h1>{b.title}</h1>{b.subtitle && <p>{b.subtitle}</p>}
          <div className="u-actions"><Btn text={b.button} link={b.link} /></div>
        </div></div>
      </section>
    );
  }
  return (
    <section className={`u-hero u-hero-${style === 'plain' || !b.image ? 'plain' : 'split'}`}>
      <div className="u-wrap u-hero-in">
        <div className="u-hero-copy">
          <h1>{b.title}</h1>
          {b.subtitle && <p>{b.subtitle}</p>}
          <div className="u-actions">
            <Btn text={b.button} link={b.link} />
            {site.contact?.whatsapp && <a className="u-btn u-btn-lg u-btn-ghost" href={waHref(site.contact.whatsapp, `Hello ${site.name}`)} target="_blank" rel="noopener">Order on WhatsApp</a>}
          </div>
          {site.delivery?.text && <div className="u-hero-note"><Icon name="truck" size={18} /> {site.delivery.text}</div>}
        </div>
        {style !== 'plain' && b.image && (
          <div className="u-hero-art">
            <div className="u-hero-blob" />
            <img src={b.image} alt="" />
            {site.delivery?.free_over > 0 && <div className="u-hero-chip"><Icon name="gift" size={18} /> Free delivery over {Number(site.delivery.free_over).toLocaleString()} {site.currency === 'ETB' ? 'Br' : site.symbol}</div>}
          </div>
        )}
      </div>
    </section>
  );
}

function Section({ title, children, more, tint }) {
  return (
    <section className={`u-section ${tint ? 'u-tint' : ''}`}>
      <div className="u-wrap">
        {(title || more) && <div className="u-section-head">{title && <h2>{title}</h2>}{more}</div>}
        {children}
      </div>
    </section>
  );
}

function Categories({ b, site }) {
  if (!site.categories?.length) return null;
  return (
    <Section title={b.title}>
      <div className="u-cats">
        {site.categories.map((c, i) => (
          <Link key={c.slug} href={`/shop?cat=${c.slug}`} className={`u-cat u-cat-${i % 4}`}>
            {c.image && <img src={c.image} alt="" />}
            <span className="u-cat-name">{c.name}</span>
            <span className="u-cat-count">{c.count} {c.count === 1 ? 'product' : 'products'} <Icon name="arrow" size={16} /></span>
          </Link>
        ))}
      </div>
    </Section>
  );
}

function Products({ b, site }) {
  if (!b.products?.length) return null;
  return (
    <Section title={b.title} more={<Link href="/shop" className="u-more">See all <Icon name="arrow" size={16} /></Link>}>
      <Grid products={b.products} site={site} />
    </Section>
  );
}

function Text({ b }) {
  return (
    <Section>
      <div className={`u-prose ${b.align === 'center' ? 'u-center' : ''}`}>
        {b.title && <h2>{b.title}</h2>}
        {(b.body || '').split(/\n{2,}/).map((para, i) => <p key={i}>{para}</p>)}
      </div>
    </Section>
  );
}

function ImageText({ b }) {
  return (
    <Section>
      <div className={`u-split ${b.side === 'right' ? 'u-split-rev' : ''}`}>
        <div className="u-split-img">{b.image ? <img src={b.image} alt="" /> : <div className="u-placeholder" />}</div>
        <div className="u-prose">
          {b.title && <h2>{b.title}</h2>}
          {(b.body || '').split(/\n{2,}/).map((para, i) => <p key={i}>{para}</p>)}
          {b.button && <div className="u-actions"><Btn text={b.button} link={b.link} /></div>}
        </div>
      </div>
    </Section>
  );
}

function Features({ b }) {
  return (
    <Section title={b.title} tint>
      <div className="u-features">
        {(b.items || []).map((it, i) => (
          <div className="u-feature" key={i}>
            <span className="u-feature-ic"><Icon name={it.icon || 'star'} size={24} /></span>
            <h3>{it.title}</h3>
            <p>{it.text}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Gallery({ b }) {
  if (!b.images?.length) return null;
  return (
    <Section title={b.title}>
      <div className="u-gallery">{b.images.map((u, i) => <img key={i} src={u} alt="" loading="lazy" />)}</div>
    </Section>
  );
}

function Testimonials({ b }) {
  return (
    <Section title={b.title}>
      <div className="u-quotes">
        {(b.items || []).map((q, i) => (
          <figure className="u-quote" key={i}>
            <Icon name="quote" size={28} />
            <blockquote>{q.quote}</blockquote>
            <figcaption><b>{q.name}</b>{q.role && <span> · {q.role}</span>}</figcaption>
          </figure>
        ))}
      </div>
    </Section>
  );
}

function Cta({ b }) {
  return (
    <section className="u-section">
      <div className="u-wrap">
        <div className="u-cta">
          <div><h2>{b.title}</h2>{b.subtitle && <p>{b.subtitle}</p>}</div>
          <Btn text={b.button} link={b.link} />
        </div>
      </div>
    </section>
  );
}

function Faq({ b }) {
  return (
    <Section title={b.title}>
      <div className="u-faq">
        {(b.items || []).map((x, i) => (
          <details key={i}><summary>{x.q}<Icon name="plus" size={18} /></summary><p>{x.a}</p></details>
        ))}
      </div>
    </Section>
  );
}

function Contact({ b, site }) {
  const c = site.contact || {};
  return (
    <Section title={b.title}>
      <div className="u-contact">
        <div className="u-contact-info">
          {b.text && <p className="u-lead">{b.text}</p>}
          {c.phone && <a className="u-contact-row" href={telHref(c.phone)}><Icon name="phone" /><span><small>Call</small>{c.phone}</span></a>}
          {c.whatsapp && <a className="u-contact-row" href={waHref(c.whatsapp)} target="_blank" rel="noopener"><Icon name="phone" /><span><small>WhatsApp</small>{c.whatsapp}</span></a>}
          {c.email && <a className="u-contact-row" href={`mailto:${c.email}`}><Icon name="mail" /><span><small>Email</small>{c.email}</span></a>}
          {c.address && <a className="u-contact-row" href={c.map_url || `https://maps.google.com/?q=${encodeURIComponent(c.address)}`} target="_blank" rel="noopener"><Icon name="pin" /><span><small>Visit</small>{c.address}</span></a>}
          {c.hours && <div className="u-contact-row"><Icon name="clock" /><span><small>Open</small>{c.hours}</span></div>}
        </div>
        {b.form !== false && <ContactForm />}
      </div>
    </Section>
  );
}

const MAP = { hero: Hero, categories: Categories, products: Products, text: Text, image_text: ImageText, features: Features,
  gallery: Gallery, testimonials: Testimonials, cta: Cta, faq: Faq, contact: Contact };

export default function Blocks({ blocks, site }) {
  return (blocks || []).map((b, i) => {
    const C = MAP[b.type];
    return C ? <div id={`b-${i}`} key={i} className="u-block"><C b={b} site={site} /></div> : null;
  });
}
