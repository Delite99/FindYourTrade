// Photography: Unsplash (free licence, attribution given in the footer).
// Images are served from the Unsplash CDN so the bundle stays small; each
// request asks for exactly the width it is rendered at.

const BASE = 'https://images.unsplash.com/';

export const photo = (id, w = 800, q = 78) => {
  if (!id) return '';
  // Dataset photos are served by the backend as absolute paths, not Unsplash ids.
  if (id.startsWith('http') || id.startsWith('/')) return id;
  return `${BASE}${id}?auto=format&fit=crop&w=${w}&q=${q}`;
};

export const HERO_SLIDES = [
  {
    id: 'farmer',
    photo: 'photo-1741874299706-2b8e16839aaa',
    kicker: 'Malawi · Verified local trade',
    title: ['Trade local.', 'Keep the kwacha.'],
    body: 'Find verified sellers near you, ranked by how much foreign exchange stays in the country. See the goods on video before any money moves.',
    credit: { name: 'Richard Nyoni', url: 'https://unsplash.com/@richard_nyoni' },
  },
  {
    id: 'oxen',
    photo: 'photo-1768775517205-7f4bc1b3f771',
    kicker: 'Forex Footprint · 40 / 30 / 15 / 15',
    title: ['Every kwacha that', 'stays home, works.'],
    body: 'Origin, supply chain, inputs and distance — a transparent score on every listing, so buying local is the easiest choice, not the worthy one.',
    credit: { name: 'BLOG REGION', url: 'https://unsplash.com/@blog_region' },
  },
  {
    id: 'road',
    photo: 'photo-1693672843238-737e13d6b86f',
    kicker: 'Show Me verification',
    title: ['See the goods', 'before you pay.'],
    body: 'A one-time code, a 30-second video, a verdict you can read. Trust is checked in the open instead of assumed.',
    credit: { name: 'Ash Willson', url: 'https://unsplash.com/@ashwillson' },
  },
];

export const CATEGORY_PHOTO = {
  fertilizer: 'photo-1709633644385-e4e51e5646f1',
  seed: 'photo-1710149484964-d966b771c204',
  maize: 'photo-1792068199928-41cab01d83e2',
  vehicle: 'photo-1559416523-140ddc3d238c',
  parts: 'photo-1759419282068-eb664e0f0a17',
  equipment: 'photo-1564868480822-32f714a0e763',
  agrochem: 'photo-1771684512143-88bdb34782fa',
  general: 'photo-1693672843111-7c74d07a9410',
};

export const CREDITS = [
  { name: 'Richard Nyoni', url: 'https://unsplash.com/@richard_nyoni' },
  { name: 'BLOG REGION', url: 'https://unsplash.com/@blog_region' },
  { name: 'Ash Willson', url: 'https://unsplash.com/@ashwillson' },
  { name: 'Dusty Barnes', url: 'https://unsplash.com/@dustbarnes' },
  { name: 'Ali Mkumbwa', url: 'https://unsplash.com/@mkumbwajr' },
  { name: 'Steve DiMatteo', url: 'https://unsplash.com/@stevedimatteo' },
  { name: 'EqualStock', url: 'https://unsplash.com/@equalstock' },
  { name: 'Walter Sturn', url: 'https://unsplash.com/@walter46' },
  { name: 'Adam Juman', url: 'https://unsplash.com/@jumanjiphotos' },
  { name: 'engin akyurt', url: 'https://unsplash.com/@enginakyurt' },
];
