import Link from 'next/link';
import { notFound } from 'next/navigation';
import { get, site as getSite } from '@/lib/odoo';
import Icon from '@/components/Icon';
import CopyText from '@/components/CopyText';
import PayAgain from '@/components/PayAgain';
import { money, telHref } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const metadata = { title: 'Your order', robots: { index: false } };

const STEPS = [['received', 'Received'], ['confirmed', 'Confirmed'], ['delivered', 'Delivered']];

export default async function Order({ params, searchParams }) {
  const { token } = await params;
  const sp = await searchParams;
  const s = await getSite();
  const o = await get(`/unio/site/api/order?t=${encodeURIComponent(token)}`, { ttl: 0 });
  if (!o) notFound();
  const at = STEPS.findIndex((x) => x[0] === o.step);
  return (
    <section className="u-section u-section-tight">
      <div className="u-wrap u-order">
        {o.awaiting_payment && (
          <div className="u-panel u-paybox">
            <h3>Payment not received yet</h3>
            <p>Your order <b>{o.name}</b> is saved. Pay {money(o.total, s)} to confirm it.</p>
            <PayAgain token={token} />
          </div>
        )}
        {(sp?.new || (o.paid && o.pay === 'chapa')) && !o.awaiting_payment && (
          <div className="u-thanks">
            <span className="u-tick"><Icon name="check" size={34} strokeWidth={2.4} /></span>
            <h1>Thank you, {o.customer.split(' ')[0]}!</h1>
            <p>{o.paid ? <>We have your payment and your order <b>{o.name}</b>. We will call you about delivery.</> : <>Your order <b>{o.name}</b> is in. We will call you to confirm.</>}</p>
          </div>
        )}
        {!sp?.new && !(o.paid && o.pay === 'chapa') && <h1>Order {o.name}</h1>}
        {o.step === 'cancelled' ? <p className="u-error">This order was cancelled.</p> : (
          <ol className="u-steps">
            {STEPS.map(([k, label], i) => <li key={k} className={i <= at ? 'done' : ''}><span>{i < at || o.step === 'delivered' ? <Icon name="check" size={14} /> : i + 1}</span>{o.how === 'pickup' && k === 'delivered' ? 'Picked up' : label}</li>)}
          </ol>
        )}
        {o.pay === 'bank' && o.bank && (
          <div className="u-panel u-paybox">
            <h3>Pay by bank transfer</h3>
            <p>Send <b>{money(o.total, s)}</b> to:</p>
            <pre>{o.bank}</pre>
            <p className="u-small u-muted">Write <b>{o.name}</b> as the reason, then send us the receipt.</p>
            <CopyText text={o.bank} />
          </div>
        )}
        {o.pay === 'telebirr' && o.telebirr && (
          <div className="u-panel u-paybox">
            <h3>Pay with telebirr</h3>
            <p>Send <b>{money(o.total, s)}</b> to <b>{o.telebirr}</b>, with <b>{o.name}</b> as the reason.</p>
            <CopyText text={o.telebirr} />
          </div>
        )}
        <div className="u-panel">
          <h3>What you ordered</h3>
          {o.lines.map((l, i) => <div className="u-row-between u-orderline" key={i}><span>{l.qty} × {l.name}</span><span>{money(l.total, s)}</span></div>)}
          <div className="u-row-between u-total"><span>Total</span><span>{money(o.total, s)}</span></div>
          <p className="u-muted u-small">{o.how === 'delivery' ? 'Delivery' : 'Pickup'} · {o.pay === 'cod' ? `Pay on ${o.how === 'delivery' ? 'delivery' : 'pickup'}` : o.pay === 'bank' ? 'Bank transfer' : o.pay === 'chapa' ? (o.paid ? 'Paid online' : 'Online payment') : 'telebirr'} · {o.date}</p>
        </div>
        <p className="u-muted u-small">Keep this page: it shows your order as it moves. {s.contact?.phone && <>Questions? <a href={telHref(s.contact.phone)}>{s.contact.phone}</a></>}</p>
        <Link className="u-btn u-btn-ghost" href="/shop">Keep shopping</Link>
      </div>
    </section>
  );
}
