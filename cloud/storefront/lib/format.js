export function money(v, site) {
  const n = Number(v || 0);
  const s = n.toLocaleString('en-US', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  const cur = site?.currency === 'ETB' ? 'Br' : site?.symbol || site?.currency || '';
  return `${s} ${cur}`.trim();
}
export function telHref(p) {
  return 'tel:' + String(p || '').replace(/[^+\d]/g, '');
}
export function waHref(p, text) {
  const n = String(p || '').replace(/[^\d]/g, '');
  return `https://wa.me/${n}${text ? '?text=' + encodeURIComponent(text) : ''}`;
}
export function tgHref(t) {
  const h = String(t || '').replace(/^@/, '').replace(/^https?:\/\/t\.me\//, '');
  return `https://t.me/${h}`;
}
