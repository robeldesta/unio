'use client';
import { useState } from 'react';
import Icon from './Icon';

export default function ContactForm() {
  const [f, setF] = useState({ name: '', phone: '', email: '', message: '', website: '' });
  const [state, setState] = useState('idle');
  const [err, setErr] = useState('');
  const send = async (e) => {
    e.preventDefault();
    setState('sending');
    const r = await fetch('/site-api/message', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(f) });
    const j = await r.json().catch(() => ({}));
    if (j.ok) setState('sent');
    else { setErr(j.error || 'Please try again.'); setState('idle'); }
  };
  if (state === 'sent') return <div className="u-panel u-sent"><Icon name="check" size={32} /><h3>Thank you</h3><p>We got your message and will answer soon.</p></div>;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="u-panel u-form" onSubmit={send}>
      <h3>Send us a message</h3>
      <label>Your name<input required value={f.name} onChange={set('name')} /></label>
      <div className="u-two">
        <label>Phone<input value={f.phone} onChange={set('phone')} inputMode="tel" /></label>
        <label>Email<input type="email" value={f.email} onChange={set('email')} /></label>
      </div>
      <label>Message<textarea required rows={4} value={f.message} onChange={set('message')} /></label>
      <input className="u-hp" tabIndex={-1} autoComplete="off" value={f.website} onChange={set('website')} aria-hidden="true" />
      {err && <p className="u-error">{err}</p>}
      <button className="u-btn u-btn-lg" disabled={state === 'sending'}>{state === 'sending' ? 'Sending…' : 'Send'}</button>
    </form>
  );
}
