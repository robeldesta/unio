'use client';
import { useState } from 'react';
import Icon from './Icon';

export default function Gallery({ images, name }) {
  const [i, setI] = useState(0);
  if (!images?.length) return <div className="u-gallery-main u-placeholder"><Icon name="bag" size={60} /></div>;
  return (
    <div className="u-pgallery">
      <div className="u-gallery-main"><img src={images[i]} alt={name} /></div>
      {images.length > 1 && (
        <div className="u-thumbs">
          {images.map((u, n) => <button key={u} className={n === i ? 'on' : ''} onClick={() => setI(n)} aria-label={`Picture ${n + 1}`}><img src={u} alt="" /></button>)}
        </div>
      )}
    </div>
  );
}
