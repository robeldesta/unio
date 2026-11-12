'use client';
// The cart lives in the visitor's browser (per shop); prices and stock are checked again by the shop at checkout.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import Icon from './Icon';
import { money } from '@/lib/format';

const Ctx = createContext(null);
const KEY = 'unio_cart_v1';

export function CartProvider({ site, children }) {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [flash, setFlash] = useState(null);

  useEffect(() => {
    try { setItems(JSON.parse(localStorage.getItem(KEY) || '[]')); } catch { /* private window */ }
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* private window */ }
  }, [items, ready]);

  const add = useCallback((p, qty = 1) => {
    setItems((list) => {
      const found = list.find((x) => x.id === p.id);
      if (found) return list.map((x) => (x.id === p.id ? { ...x, qty: x.qty + qty } : x));
      return [...list, { id: p.id, slug: p.slug, name: p.name, price: p.price, image: p.image, qty, max: p.stock?.left ?? null }];
    });
    setFlash(p.name);
    setOpen(true);
  }, []);
  const setQty = useCallback((id, qty) => {
    setItems((list) => (qty <= 0 ? list.filter((x) => x.id !== id) : list.map((x) => (x.id === id ? { ...x, qty } : x))));
  }, []);
  const clear = useCallback(() => setItems([]), []);
  const count = items.reduce((s, x) => s + x.qty, 0);
  const total = items.reduce((s, x) => s + x.qty * x.price, 0);
  const value = useMemo(() => ({ items, add, setQty, clear, count, total, open, setOpen, ready, flash }),
    [items, add, setQty, clear, count, total, open, ready, flash]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <CartDrawer site={site} />
    </Ctx.Provider>
  );
}

export function useCart() {
  return useContext(Ctx);
}

export function CartButton() {
  const cart = useCart();
  return (
    <button className="u-cartbtn" onClick={() => cart.setOpen(true)} aria-label={`Cart, ${cart.count} items`}>
      <Icon name="bag" />
      {cart.ready && cart.count > 0 && <span className="u-cartcount">{cart.count}</span>}
    </button>
  );
}

export function Stepper({ value, onChange, max }) {
  return (
    <span className="u-stepper">
      <button onClick={() => onChange(value - 1)} aria-label="One less"><Icon name="minus" size={16} /></button>
      <span>{value}</span>
      <button onClick={() => onChange(max != null ? Math.min(value + 1, max) : value + 1)} disabled={max != null && value >= max} aria-label="One more"><Icon name="plus" size={16} /></button>
    </span>
  );
}

function CartDrawer({ site }) {
  const cart = useCart();
  const free = site?.delivery?.free_over;
  const left = free ? Math.max(free - cart.total, 0) : 0;
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && cart.setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [cart]);
  return (
    <div className={`u-drawer ${cart.open ? 'open' : ''}`} aria-hidden={!cart.open}>
      <div className="u-drawer-shade" onClick={() => cart.setOpen(false)} />
      <aside className="u-drawer-panel" role="dialog" aria-label="Your cart">
        <header>
          <h2>Your cart</h2>
          <button className="u-iconbtn" onClick={() => cart.setOpen(false)} aria-label="Close"><Icon name="close" /></button>
        </header>
        {free > 0 && cart.count > 0 && site?.delivery?.delivery && (
          <div className="u-freebar">
            {left > 0 ? <>Add <b>{money(left, site)}</b> more for free delivery</> : <><Icon name="check" size={16} /> Free delivery</>}
            <span><i style={{ width: `${Math.min((cart.total / free) * 100, 100)}%` }} /></span>
          </div>
        )}
        <div className="u-drawer-items">
          {cart.items.length === 0 && (
            <div className="u-empty">
              <Icon name="bag" size={40} />
              <p>Your cart is empty.</p>
              <Link className="u-btn" href="/shop" onClick={() => cart.setOpen(false)}>Start shopping</Link>
            </div>
          )}
          {cart.items.map((x) => (
            <div className="u-line" key={x.id}>
              <Link href={`/product/${x.slug}`} onClick={() => cart.setOpen(false)} className="u-line-img">
                {x.image ? <img src={x.image} alt="" /> : <Icon name="bag" />}
              </Link>
              <div className="u-line-body">
                <Link href={`/product/${x.slug}`} onClick={() => cart.setOpen(false)}>{x.name}</Link>
                <div className="u-line-price">{money(x.price, site)}</div>
                <Stepper value={x.qty} max={x.max} onChange={(q) => cart.setQty(x.id, q)} />
              </div>
              <b>{money(x.qty * x.price, site)}</b>
            </div>
          ))}
        </div>
        {cart.items.length > 0 && (
          <footer>
            <div className="u-row-between"><span>Subtotal</span><b>{money(cart.total, site)}</b></div>
            <p className="u-muted u-small">Delivery is added at checkout.</p>
            <Link className="u-btn u-btn-block u-btn-lg" href="/checkout" onClick={() => cart.setOpen(false)}>Checkout <Icon name="arrow" size={18} /></Link>
          </footer>
        )}
      </aside>
    </div>
  );
}

export function AddToCart({ product, site, big = false }) {
  const cart = useCart();
  const [qty, setQty] = useState(1);
  const out = product.stock?.state === 'out';
  if (!site?.shop) return null;
  if (!big) {
    return (
      <button className="u-quickadd" disabled={out} onClick={(e) => { e.preventDefault(); cart.add(product, 1); }}
        aria-label={`Add ${product.name} to the cart`}>
        <Icon name="plus" size={18} />
      </button>
    );
  }
  return (
    <div className="u-buy">
      <Stepper value={qty} max={product.stock?.left ?? null} onChange={(q) => setQty(Math.max(q, 1))} />
      <button className="u-btn u-btn-lg u-grow" disabled={out} onClick={() => cart.add(product, qty)}>
        {out ? 'Out of stock' : <>Add to cart · {money(product.price * qty, site)}</>}
      </button>
    </div>
  );
}
