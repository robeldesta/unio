import { redirect } from 'next/navigation';

/** A company without a website online: its address keeps opening its Unio system, as before. */
export default function Offline() {
  redirect('/app');
}
