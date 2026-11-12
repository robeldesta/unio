'use client';
import { useState } from 'react';

export default function PayAgain({ token }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const go = async () => {
    setBusy(true);
    setErr('');
    const r = await fetch('/site-api/pay', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    const j = await r.json().catch(() => ({}));
    if (j.pay_url) { window.location.href = j.pay_url; return; }
    setErr(j.error || 'Please try again.');
    setBusy(false);
  };
  return (
    <>
      <button className="u-btn u-btn-lg" onClick={go} disabled={busy}>{busy ? 'Opening…' : 'Pay now'}</button>
      {err && <p className="u-error">{err}</p>}
    </>
  );
}
