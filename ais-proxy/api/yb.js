// Yellow Brick (YB) -> RAR dashboard proxy
// Fetches a YB race's RaceSetup (JSON) and AllPositions3 (binary) server-side
// and returns them with CORS headers, so the browser dashboard / safety-boat
// view can pull the feed from anywhere (Starlink on the water) without hitting
// a cross-origin block from yb.tl.
//
// Deploy on Vercel alongside api/ais.js (same "jwds" project).
// Endpoints:
//   https://<project>.vercel.app/api/yb?race=RAR2026&feed=RaceSetup     -> JSON
//   https://<project>.vercel.app/api/yb?race=RAR2026&feed=AllPositions3 -> binary
//
// The client (YB.loadRace with proxyBase) builds exactly those URLs.

// feed -> upstream yb.tl path. Allowlisted so this can't be used as an open proxy.
const FEEDS = {
  RaceSetup:     (race) => `https://yb.tl/JSON/${race}/RaceSetup`,
  AllPositions3: (race) => `https://yb.tl/BIN/${race}/AllPositions3`,
};

// Positions change often; the setup (course, team list) rarely. Cache the setup
// harder so the fleet list isn't re-fetched on every 60s poll.
const CACHE = {
  RaceSetup:     's-maxage=120, stale-while-revalidate=60',
  AllPositions3: 's-maxage=20, stale-while-revalidate=20',
};

const RACE_RE = /^[A-Za-z0-9_-]{1,64}$/;

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');

  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const race = (req.query.race || '').toString();
  const feed = (req.query.feed || 'RaceSetup').toString();

  if (!RACE_RE.test(race)) {
    res.status(400).json({ error: 'Bad or missing ?race= (alphanumeric, - and _ only)' });
    return;
  }
  if (!FEEDS[feed]) {
    res.status(400).json({ error: `Unknown ?feed=${feed}. Use RaceSetup or AllPositions3.` });
    return;
  }

  const url = FEEDS[feed](race);

  try {
    const upstream = await fetch(url);
    res.setHeader('Cache-Control', CACHE[feed]);

    // Pass upstream status through so the client sees 404s for a bad race key
    // (e.g. before YB switches the feed on ~2 days pre-race).
    if (feed === 'AllPositions3') {
      const buf = Buffer.from(await upstream.arrayBuffer());
      res.setHeader('Content-Type', 'application/octet-stream');
      res.status(upstream.status).send(buf);
    } else {
      const text = await upstream.text();
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.status(upstream.status).send(text);
    }
  } catch (e) {
    res.status(502).json({ error: 'Upstream fetch failed', detail: String(e), url });
  }
}
