'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useCart, Stepper } from './Cart';
import Icon from './Icon';
import { money } from '@/lib/format';

export default function Checkout({ site }) {
  const cart = useCart();
  const router = useRouter();
  const d = site.delivery || {};
  const pay = site.payment || {};
  const [f, setF] = useState({
    name: '', phone: '', email: '', address: '', city: '', note: '', website: '',
    how: d.delivery ? 'delivery' : 'pickup', pay: pay.cod ? 'cod' : pay.bank ? 'bank' : 'telebirr',
  });
  const [quote, setQuote] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('unio_me') || '{}');
      setF((x) => ({ ...x, ...saved }));
    } catch { /* private window */ }
  }, []);

  const cartKey = JSON.stringify(cart.items.map((x) => [x.id, x.qty]));
  useEffect(() => {
    if (!cart.ready) return;
    if (!cart.items.length) { setQuote(null); return; }
    const ctl = new AbortController();
    fetch('/site-api/quote', { method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cart: cart.items.map((x) => ({ id: x.id, qty: x.qty })), how: f.how }) })
      .then((r) => r.json()).then(setQuote).catch(() => {});
    return () => ctl.abort();
  }, [cartKey, f.how, cart.ready]);

  const place = async (e) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      localStorage.setItem('unio_me', JSON.stringify({ name: f.name, phone: f.phone, email: f.email, address: f.address, city: f.city }));
    } catch { /* private window */ }
    const r = await fetch('/site-api/order', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...f, cart: cart.items.map((x) => ({ id: x.id, qty: x.qty })) }) });
    const j = await r.json().catch(() => ({ ok: false }));
    if (j.ok) {
      cart.clear();
      if (j.pay_url) {
        window.location.href = j.pay_url;  // Chapa's payment page; it brings the buyer back to the order page
        return;
      }
      router.push(`/order/${j.token}?new=1`);
      return;
    }
    if (j.problems) {
      for (const p of j.problems) if (p.problem === 'stock') cart.setQty(p.id, p.left);
      setErr('Some items have less stock than you asked for; we changed the quantities. Please check and order again.');
    } else {
      setErr(j.error || 'Something went wrong. Please try again, or call us.');
    }
    setBusy(false);
  };

  if (cart.ready && !cart.items.length) {
    return (
      <section className="u-section"><div className="u-wrap u-offline">
        <Icon name="bag" size={44} /><h1>Your cart is empty</h1>
        <Link className="u-btn" href="/shop">Go to the shop</Link>
      </div></section>
    );
  }
  const problems = quote?.problems?.filter((p) => p.problem === 'stock') || [];
  return (
    <section className="u-section u-section-tight">
      <div className="u-wrap">
        <h1 className="u-checkout-title">Checkout</h1>
        <form className="u-checkout" onSubmit={place}>
          <div className="u-checkout-main">
            <div className="u-panel">
              <h3><span className="u-stepno">1</span> Your details</h3>
              <label>Full name<input required value={f.name} onChange={set('name')} autoComplete="name" /></label>
              <div className="u-two">
                <label>Phone<input required value={f.phone} onChange={set('phone')} inputMode="tel" autoComplete="tel" placeholder="09…" /></label>
                <label>Email <small>(for the receipt, optional)</small><input type="email" value={f.email} onChange={set('email')} autoComplete="email" /></label>
              </div>
            </div>
            <div className="u-panel">
              <h3><span className="u-stepno">2</span> Delivery or pickup</h3>
              <div className="u-options">
                {d.delivery && (
                  <label className={`u-option ${f.how === 'delivery' ? 'on' : ''}`}>
                    <input type="radio" name="how" checked={f.how === 'delivery'} onChange={() => setF({ ...f, how: 'delivery' })} />
                    <Icon name="truck" /><span><b>Delivery</b><small>{d.text || 'To your door'}</small></span>
                    <em>{quote && f.how === 'delivery' ? (quote.delivery ? money(quote.delivery, site) : 'Free') : money(d.fee, site)}</em>
                  </label>
                )}
                {d.pickup && (
                  <label className={`u-option ${f.how === 'pickup' ? 'on' : ''}`}>
                    <input type="radio" name="how" checked={f.how === 'pickup'} onChange={() => setF({ ...f, how: 'pickup' })} />
                    <Icon name="store" /><span><b>Pickup</b><small>{d.pickup_text || site.contact?.address}</small></span>
                    <em>Free</em>
                  </label>
                )}
              </div>
              {f.how === 'delivery' && (
                <div className="u-two">
                  <label className="u-span2">Address<input required value={f.address} onChange={set('address')} autoComplete="street-address" placeholder="Area, street, building, landmark" /></label>
                  <label>City<input value={f.city} onChange={set('city')} autoComplete="address-level2" /></label>
                </div>
              )}
            </div>
            <div className="u-panel">
              <h3><span className="u-stepno">3</span> Payment</h3>
              <div className="u-options">
                {pay.chapa && (
                  <label className={`u-option ${f.pay === 'chapa' ? 'on' : ''}`}>
                    <input type="radio" name="pay" checked={f.pay === 'chapa'} onChange={() => setF({ ...f, pay: 'chapa' })} />
                    <Icon name="shield" /><span><b>Pay now</b><small>telebirr, CBE Birr, M-Pesa or card, through Chapa</small></span>
                  </label>
                )}
                {pay.cod && (
                  <label className={`u-option ${f.pay === 'cod' ? 'on' : ''}`}>
                    <input type="radio" name="pay" checked={f.pay === 'cod'} onChange={() => setF({ ...f, pay: 'cod' })} />
                    <Icon name="cash" /><span><b>Pay when you get it</b><small>Cash or telebirr on {f.how === 'delivery' ? 'delivery' : 'pickup'}</small></span>
                  </label>
                )}
                {pay.telebirr && (
                  <label className={`u-option ${f.pay === 'telebirr' ? 'on' : ''}`}>
                    <input type="radio" name="pay" checked={f.pay === 'telebirr'} onChange={() => setF({ ...f, pay: 'telebirr' })} />
                    <Icon name="phone" /><span><b>telebirr transfer</b><small>We show you where to send it</small></span>
                  </label>
                )}
                {pay.bank && (
                  <label className={`u-option ${f.pay === 'bank' ? 'on' : ''}`}>
                    <input type="radio" name="pay" checked={f.pay === 'bank'} onChange={() => setF({ ...f, pay: 'bank' })} />
                    <Icon name="shield" /><span><b>Bank transfer</b><small>We show you the account after ordering</small></span>
                  </label>
                )}
              </div>
              <label>Note <small>(optional)</small><textarea rows={2} value={f.note} onChange={set('note')} placeholder="Delivery time, gift message…" /></label>
              <input className="u-hp" tabIndex={-1} autoComplete="off" value={f.website} onChange={set('website')} aria-hidden="true" />
            </div>
          </div>
          <aside className="u-summary">
            <div className="u-panel">
              <h3>Your order</h3>
              {cart.items.map((x) => (
                <div className="u-sumline" key={x.id}>
                  <span className="u-sumimg">{x.image ? <img src={x.image} alt="" /> : null}<i>{x.qty}</i></span>
                  <span className="u-grow">{x.name}<br /><Stepper value={x.qty} max={x.max} onChange={(q) => cart.setQty(x.id, q)} /></span>
                  <b>{money(x.qty * x.price, site)}</b>
                </div>
              ))}
              {problems.length > 0 && <p className="u-error">Some items have less stock than in your cart.</p>}
              <div className="u-sumtotals">
                <div className="u-row-between"><span>Items</span><span>{quote ? money(quote.subtotal + quote.tax, site) : money(cart.total, site)}</span></div>
                {quote?.tax > 0 && <div className="u-row-between u-muted u-small"><span>of which VAT</span><span>{money(quote.tax, site)}</span></div>}
                {f.how === 'delivery' && <div className="u-row-between"><span>Delivery</span><span>{quote ? (quote.delivery ? money(quote.delivery, site) : 'Free') : '…'}</span></div>}
                <div className="u-row-between u-total"><span>Total</span><span>{quote ? money(quote.total, site) : '…'}</span></div>
              </div>
              {quote?.below_min && <p className="u-error">The smallest order is {money(quote.min_order, site)}.</p>}
              {err && <p className="u-error">{err}</p>}
              <button className="u-btn u-btn-lg u-btn-block" disabled={busy || !quote || quote.below_min}>
                {busy ? (f.pay === 'chapa' ? 'Opening the payment page…' : 'Placing your order…')
                  : <>{f.pay === 'chapa' ? 'Pay' : 'Place order'} · {quote ? money(quote.total, site) : ''}</>}
              </button>
              <p className="u-muted u-small u-center">We call you to confirm before we deliver.</p>
              {site.terms && <details className="u-terms"><summary>Terms of sale</summary><p>{site.terms}</p></details>}
            </div>
          </aside>
        </form>
      </div>
    </section>
  );
}
