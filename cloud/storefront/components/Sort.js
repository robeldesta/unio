'use client';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export default function Sort({ value }) {
  const router = useRouter();
  const path = usePathname();
  const sp = useSearchParams();
  const change = (e) => {
    const n = new URLSearchParams(sp.toString());
    if (e.target.value) n.set('sort', e.target.value); else n.delete('sort');
    n.delete('page');
    router.push(`${path}?${n}`);
  };
  return (
    <select className="u-select" value={value} onChange={change} aria-label="Sort">
      <option value="">Recommended</option>
      <option value="new">Newest</option>
      <option value="price">Price: low to high</option>
      <option value="price_desc">Price: high to low</option>
      <option value="name">Name</option>
    </select>
  );
}
