// AISHub -> RAR dashboard proxy
// Fetches aggregated AIS data from AISHub server-side (hiding the API key),
// reshapes it into the dashboard's AIS_VESSELS format, and returns it with
// CORS headers so the browser dashboard can fetch it from anywhere (Starlink).
//
// Deploy on Vercel. Set env var AISHUB_USERNAME to your AISHub API username/key.
// Endpoint: https://<your-project>.vercel.app/api/ais
//
// NOTE: AISHub allows at most one request per minute or it returns nothing and
// you risk losing your key. The Cache-Control below makes Vercel's CDN serve a
// cached response for 90s, so AISHub is queried at most ~once/90s no matter how
// many dashboard clients poll or how often.

// Bounding box around the Race Around the Rock course (Salt Spring / Gulf Islands).
const BBOX = { latmin: 48.55, latmax: 49.15, lonmin: -123.75, lonmax: -123.10 };

// AIS numeric ship-type code -> dashboard category.
// Categories used by the dashboard: ferry, coastguard, military_law, cargo,
// fishing, underway, other. renderAIS() only plots the "commercial" ones.
function typeToCat(type, name) {
  const t = Number(type) || 0;
  const n = (name || '').toUpperCase();

  // Name-based hints first — gov vessels often don't set a useful type code.
  if (n.startsWith('CCGS') || n.includes('COAST GUARD')) return 'coastguard';

  if (t >= 60 && t <= 69) return 'ferry';        // passenger (BC Ferries)
  if (t >= 70 && t <= 79) return 'cargo';        // cargo
  if (t >= 80 && t <= 89) return 'cargo';        // tankers, grouped with cargo
  if (t === 30) return 'fishing';                // fishing
  if (t === 51) return 'coastguard';             // search & rescue
  if (t === 55 || t === 35) return 'military_law'; // law enforcement / military
  if (t > 0) return 'underway';                  // known but uncategorized
  return 'other';
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=90, stale-while-revalidate=30');

  const user = process.env.AISHUB_USERNAME;
  if (!user) {
    res.status(500).json({ error: 'AISHUB_USERNAME env var not set' });
    return;
  }

  const url =
    `https://data.aishub.net/ws.php?username=${encodeURIComponent(user)}` +
    `&format=1&output=json&compress=0` +
    `&latmin=${BBOX.latmin}&latmax=${BBOX.latmax}` +
    `&lonmin=${BBOX.lonmin}&lonmax=${BBOX.lonmax}`;

  try {
    const r = await fetch(url);
    const data = await r.json();

    // AISHub format=1 JSON is [ {ERROR, RECORDS, ...}, [ {vessel}, ... ] ].
    // VERIFY this shape against the first real response once the key is live —
    // AISHub occasionally nests differently and field casing must be confirmed.
    const meta = Array.isArray(data) ? data[0] : {};
    if (meta && meta.ERROR) {
      res.status(502).json({ error: meta.ERROR_MESSAGE || 'AISHub error', meta });
      return;
    }
    const rows = (Array.isArray(data) && Array.isArray(data[1])) ? data[1] : [];

    const vessels = rows
      .map((v) => ({
        mmsi: String(v.MMSI ?? ''),
        name: (v.NAME ?? '').trim(),
        lat: Number(v.LATITUDE),
        lon: Number(v.LONGITUDE),
        sog: Number(v.SOG ?? 0),
        cog: Number(v.COG ?? 0),
        cat: typeToCat(v.TYPE, v.NAME),
        dest: (v.DEST ?? '').trim(),
      }))
      .filter((v) => Number.isFinite(v.lat) && Number.isFinite(v.lon));

    res.status(200).json({
      updated: new Date().toISOString(),
      count: vessels.length,
      vessels,
    });
  } catch (e) {
    res.status(502).json({ error: String(e) });
  }
}
