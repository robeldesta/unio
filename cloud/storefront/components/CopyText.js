'use client';
import { useState } from 'react';
import Icon from './Icon';

export default function CopyText({ text }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 2000); } catch { /* select by hand */ }
  };
  return <button type="button" className="u-btn u-btn-sm u-btn-ghost" onClick={copy}><Icon name={done ? 'check' : 'copy'} size={16} /> {done ? 'Copied' : 'Copy'}</button>;
}
