// Pictures come from the company's system. In production the web server sends /unio/* straight there; this route
// only answers when the storefront runs on its own (e.g. while developing).
import { pipe } from '@/lib/pipe';

export async function GET(req, { params }) {
  const { path } = await params;
  return pipe(`/unio/${path.map(encodeURIComponent).join('/')}${new URL(req.url).search}`);
}

