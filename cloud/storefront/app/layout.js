import './globals.css';
import { redirect } from 'next/navigation';
import { site as getSite } from '@/lib/odoo';
import { fontVars, FONT_SETS } from './fonts';
import { CartProvider } from '@/components/Cart';
import Header from '@/components/Header';
import Footer from '@/components/Footer';
import Hit from '@/components/Hit';

export async function generateMetadata() {
  const s = await getSite();
  if (!s) return { title: 'Shop' };
  return {
    title: { default: s.seo?.title || s.name, template: `%s · ${s.name}` },
    description: s.seo?.description || s.tagline,
    icons: s.logo ? { icon: s.logo } : undefined,
    openGraph: { siteName: s.name, type: 'website' },
  };
}

export const viewport = { width: 'device-width', initialScale: 1, themeColor: '#ffffff' };

function shade(hex, amt) {
  // lighter (amt > 0) or darker (amt < 0) version of a colour, for hovers and tints
  const n = parseInt((hex || '#1F6F5C').replace('#', ''), 16);
  const f = (c) => Math.max(0, Math.min(255, Math.round(amt > 0 ? c + (255 - c) * amt : c * (1 + amt))));
  const r = f(n >> 16), g = f((n >> 8) & 255), b = f(n & 255);
  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
}

export default async function RootLayout({ children }) {
  const s = await getSite();
  if (!s) redirect('/app');
  const [body, display] = FONT_SETS[s.font] || FONT_SETS.inter;
  const style = {
    '--brand': s.color, '--brand-d': shade(s.color, -0.25), '--brand-l': shade(s.color, 0.88), '--brand-xl': shade(s.color, 0.95),
    '--accent': s.accent, '--accent-l': shade(s.accent, 0.82), '--font': body, '--display': display,
  };
  return (
    <html lang="en" className={fontVars}>
      <body data-theme={s.theme} style={style}>
        <CartProvider site={s}>
          {s.announcement && <div className="u-announce">{s.announcement}</div>}
          <Header site={s} />
          <main className="u-main">{children}</main>
          <Footer site={s} />
        </CartProvider>
        <Hit />
      </body>
    </html>
  );
}
