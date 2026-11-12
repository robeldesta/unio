import Link from 'next/link';

export default function NotFound() {
  return (
    <section className="u-section"><div className="u-wrap u-offline">
      <h1>Not found</h1>
      <p>This page does not exist, or it has moved.</p>
      <Link className="u-btn" href="/">Go to the home page</Link>
    </div></section>
  );
}
