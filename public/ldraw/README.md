# LDraw library

Drop the LDraw parts library here for the viewer to render arbitrary models.

```bash
# from repo root
curl -L -o /tmp/ldraw_complete.zip https://library.ldraw.org/library/updates/complete.zip
unzip /tmp/ldraw_complete.zip -d public/ldraw_unpacked
# Three.js LDrawLoader expects parts/ and p/ directly here:
mv public/ldraw_unpacked/ldraw/parts public/ldraw/parts
mv public/ldraw_unpacked/ldraw/p     public/ldraw/p
mv public/ldraw_unpacked/ldraw/LDConfig.ldr public/ldraw/LDConfig.ldr
rm -rf public/ldraw_unpacked
```

The library is ~250 MB unpacked and is gitignored. For now the viewer falls back
to a Three.js-hosted sample model so it's demoable without local setup.

After ingestion, also run the Supabase ingest:

```bash
cd worker
python ingest_ldraw.py --library-path ../public/ldraw
```
