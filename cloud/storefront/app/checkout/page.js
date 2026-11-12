import { site as getSite } from '@/lib/odoo';
import Checkout from '@/components/Checkout';
import Offline from '@/components/Offline';

export const dynamic = 'force-dynamic';
export const metadata = { title: 'Checkout', robots: { index: false } };

export default async function CheckoutPage() {
  const s = await getSite();
  if (!s.online || !s.shop) return <Offline site={s} />;
  return <Checkout site={s} />;
}
