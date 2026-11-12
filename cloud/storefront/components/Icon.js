// Line icons drawn for the storefront (24 x 24, stroke = currentColor).
const P = {
  cart: 'M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6.2M10 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm8 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2-4.3-4.3',
  menu: 'M4 7h16M4 12h16M4 17h16',
  close: 'M6 6l12 12M18 6 6 18',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  back: 'M19 12H5m6 6-6-6 6-6',
  truck: 'M3 6h11v10H3zM14 9h4l3 3v4h-7M7.5 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm10 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
  shield: 'M12 3 5 6v6c0 4.4 3 8.2 7 9 4-.8 7-4.6 7-9V6zm-3 9 2 2 4-4',
  cash: 'M3 7h18v10H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 10v4m12-4v4',
  star: 'm12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-13v5l3 2',
  leaf: 'M5 19c0-8 5-13 15-14-1 10-6 15-14 15M5 19l7-7',
  heart: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z',
  phone: 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z',
  gift: 'M4 11h16v10H4zM3 7h18v4H3zM12 7v14M12 7S10 3 8 3.5 7 7 12 7zm0 0s2-4 4-3.5S17 7 12 7z',
  check: 'M5 12.5 10 17 19 7',
  pin: 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zm0-8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  mail: 'M3 6h18v12H3zm0 0 9 7 9-7',
  bag: 'M5 8h14l-1 13H6zm4 0V6a3 3 0 0 1 6 0v2',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  store: 'M4 9 5.5 4h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6',
  quote: 'M7 17c-2 0-3-1.5-3-4 0-3 2-6 5-7l.7 1.3C8 8.3 7 10 7 11.5h2V17zm9 0c-2 0-3-1.5-3-4 0-3 2-6 5-7l.7 1.3C17 8.3 16 10 16 11.5h2V17z',
};
const FILLED = { quote: true, star: false };

export default function Icon({ name, size = 22, className = '', strokeWidth = 1.8 }) {
  const d = P[name] || P.star;
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"
      fill={FILLED[name] ? 'currentColor' : 'none'} stroke={FILLED[name] ? 'none' : 'currentColor'}
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

export function Social({ name, size = 20 }) {
  const paths = {
    whatsapp: 'M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm5.2 13.9c-.2.6-1.3 1.2-1.8 1.2-.5.1-1 .2-3.3-.7-2.8-1.1-4.5-3.9-4.7-4.1-.1-.2-1.1-1.5-1.1-2.9s.7-2.1 1-2.4c.3-.3.6-.3.8-.3h.6c.2 0 .4 0 .6.5l.9 2.1c.1.2.1.4 0 .5l-.4.6-.4.4c-.1.1-.3.3-.1.6.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.4 2.4 1.5.3.1.5.1.6-.1l.9-1.1c.2-.3.4-.2.7-.1l2 1c.3.1.5.2.5.3.1.2.1.7-.1 1.3z',
    telegram: 'M21.5 3.6 2.9 10.8c-1.3.5-1.2 1.2-.2 1.5l4.8 1.5 1.8 5.6c.2.6.4.8.9.8s.6-.2.9-.4l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.9-1.4-1.6zM9.4 13.6l9-5.7c.4-.3.8-.1.5.2l-7.6 6.9-.3 3.2z',
    facebook: 'M14 8h3V4h-3c-2.8 0-4 1.7-4 4.2V10H7v4h3v8h4v-8h3l.6-4H14V8.6c0-.4.2-.6.6-.6z',
    instagram: 'M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9zm0 7.4a2.9 2.9 0 1 1 0-5.8 2.9 2.9 0 0 1 0 5.8zM17.6 5.3a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2zM12 3.6c2.7 0 3 0 4.1.1 2.7.1 4.1 1.5 4.2 4.2.1 1.1.1 1.4.1 4.1s0 3-.1 4.1c-.1 2.7-1.5 4.1-4.2 4.2-1.1.1-1.4.1-4.1.1s-3 0-4.1-.1c-2.7-.1-4.1-1.5-4.2-4.2-.1-1.1-.1-1.4-.1-4.1s0-3 .1-4.1C3.8 5.2 5.2 3.8 7.9 3.7c1.1-.1 1.4-.1 4.1-.1z',
    tiktok: 'M16.5 3c.4 2.2 1.8 3.6 4 3.8v3.2c-1.5 0-2.8-.4-4-1.2v6.4A6 6 0 1 1 10.5 9v3.3a2.8 2.8 0 1 0 2.8 2.8V3z',
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d={paths[name]} /></svg>;
}
