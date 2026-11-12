import { pipe } from '@/lib/pipe';

export async function GET(req, { params }) {
  const { path } = await params;
  return pipe(`/web/image/${path.map(encodeURIComponent).join('/')}`);
}
