# RAR AIS Proxy

A tiny Vercel serverless function that sits between AISHub's API and the RAR race
control dashboard. It exists to solve the race-day problem: on race day you're on
the course with Starlink, not on the Pi's home network, so the dashboard can't
reach the Pi directly. Instead:

```
Pi (home internet)  ──UDP──►  AISHub servers
                                   │
                          this proxy (Vercel)  ── polls AISHub ≤ once/90s
                                   │
                   dashboard (laptop, Starlink)  ── polls proxy every 60s
```

The proxy:
- queries AISHub server-side so your API key never sits in the browser
- reshapes AISHub's response into the dashboard's `{mmsi,name,lat,lon,sog,cog,cat,dest}` format
- adds CORS headers so the browser can fetch it from anywhere
- caches for 90s at the CDN so AISHub is never hit more than once/minute (which would risk your key)

## Deploy (your own Vercel account — recommended)

1. Create a free account at vercel.com.
2. Install the CLI: `npm i -g vercel`
3. From this `ais-proxy/` folder, run `vercel` and follow the prompts (accept defaults).
4. Set your AISHub API username as an environment variable:
   `vercel env add AISHUB_USERNAME`
   (paste the username/key AISHub issues once your station is verified)
5. Redeploy to production: `vercel --prod`
6. Your endpoint is: `https://<your-project>.vercel.app/api/ais`

Test it in a browser — you should get JSON like:
`{"updated":"...","count":34,"vessels":[{"mmsi":"316001267","name":"SKEENA QUEEN",...}]}`

## Before it works end-to-end

You need your **AISHub API username**, issued after your receiving station is
verified as a contributor (their bar: ~10 vessels avg over 7 days, ~90% uptime).
Until then the function deploys fine but returns an auth error from AISHub.

## Verify the response shape

AISHub's `format=1` JSON is documented as `[ {meta}, [ {vessel}, ... ] ]`, but
confirm the real field names and nesting against the first live response and
adjust the mapping in `api/ais.js` if needed before relying on it for the race.

## Tuning

- `BBOX` in `api/ais.js` sets the geographic area pulled. Currently the Salt
  Spring / Gulf Islands course. Widen or narrow as needed.
- `typeToCat()` maps AIS ship-type codes to the dashboard's categories. Adjust
  once you see what real traffic reports.
