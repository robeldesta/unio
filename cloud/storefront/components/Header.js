'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import Icon from './Icon';
import { CartButton } from './Cart';

export default function Header({ site }) {
  const path = usePathname();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState(false);
  const [q, setQ] = useState('');
  useEffect(() => { setMenu(false); setSearch(false); }, [path]);
  const links = [{ name: 'Home', path: '/' }, ...(site.shop ? [{ name: 'Shop', path: '/shop' }] : []), ...site.menu.filter((m) => m.path !== '/')];
  const go = (e) => {
    e.preventDefault();
    if (q.trim()) router.push(`/shop?q=${encodeURIComponent(q.trim())}`);
  };
  return (
    <header className="u-header">
      <div className="u-wrap u-header-in">
        <button className="u-iconbtn u-only-mobile" onClick={() => setMenu(!menu)} aria-label="Menu"><Icon name={menu ? 'close' : 'menu'} /></button>
        <Link href="/" className="u-brand">
          {site.logo ? <img src={site.logo} alt={site.name} /> : null}
          <span>{site.name}</span>
        </Link>
        <nav className={`u-nav ${menu ? 'open' : ''}`}>
          {links.map((l) => (
            <Link key={l.path} href={l.path} className={(l.path === '/' ? path === '/' : path.startsWith(l.path)) ? 'active' : ''}>{l.name}</Link>
          ))}
        </nav>
        <div className="u-header-tools">
          {site.shop && <button className="u-iconbtn" onClick={() => setSearch(!search)} aria-label="Search"><Icon name="search" /></button>}
          {site.shop && <CartButton />}
        </div>
      </div>
      {search && (
        <form className="u-searchbar u-wrap" onSubmit={go}>
          <Icon name="search" size={18} />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" />
          <button className="u-btn u-btn-sm" type="submit">Search</button>
        </form>
      )}
    </header>
  );
}
