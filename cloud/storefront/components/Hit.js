'use client';
import { usePathname } from 'next/navigation';
import { useEffect } from 'react';

/** Counts a visit per page (no cookies, nothing personal). */
export default function Hit() {
  const path = usePathname();
  useEffect(() => {
    // in the page builder's preview: scroll to the block being edited
    const onMsg = (e) => {
      const id = e.data && e.data.unio_scroll;
      if (id) document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, []);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('preview') || path.startsWith('/order/') || path === '/checkout') return;
    fetch('/site-api/hit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }), keepalive: true }).catch(() => {});
  }, [path]);
  return null;
}
