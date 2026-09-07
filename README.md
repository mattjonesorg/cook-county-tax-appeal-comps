# Cook County Tax Appeal Comps

Find Cook County property tax appeal comparables for a given PIN, straight
from the county's own ArcGIS and open-data APIs -- no manual CSV export from
CookViewer required.

## Quick start

```
python3 comps.py 17-01-234-567-0000
python3 comps.py 17012345670000 --out-dir output/custom-run
```

Requires `requests` and `pandas` (`pip install requests pandas`).

Give it a PIN (PIN10 or PIN14, with or without dashes) and it will:

1. Look up the property's own characteristics (class, construction type,
   square footage, age, assessed value) and exact parcel geometry from the
   Assessor's `CookViewer3Parcels` ArcGIS layer.
2. Search for comparable properties using the same default criteria
   CookViewer's own "Comparable Properties" tool applies: same class,
   township, and Assessor neighborhood code; within 0.5 miles; within 10% of
   building/land square footage; within 15 years of building age; same
   construction type.
3. Cross-reference each comp against two more Cook County Open Data
   datasets:
   - **Assessor - Parcel Sales** -- flags a comp if it sold in the last 5
     years for less than its current assessment implies (scaled to Cook
     County's 10% residential assessment level).
   - **Board of Review Appeal Decision History** -- flags a comp if it was
     already reduced on a prior appeal (the strongest evidence: the County
     has already agreed that PIN was over-assessed), and reports your own
     property's last appeal outcome for context.
4. Filters to comps assessed *lower* per square foot than your property,
   ranks the strongest evidence first, and writes CSVs plus a ready-to-use
   narrative for the appeal form.

Pass `--no-enrich` to skip the sales/appeal-history lookups (faster, but
without the extra evidence).

### Output

By default, results are written to `output/<assessment year>/` (the year is
read from the property's own assessment data, e.g. `output/2026/`; override
with `--out-dir`). For PIN `17-01-234-567-0000` that's:

- `17012345670000-my-property.csv` -- your property's own data
- `17012345670000-comparables-all.csv` -- every comp found, before filtering
- `17012345670000-comparables-building.csv` -- comps cheaper per sqft of
  building value than yours, sorted strongest-evidence-first
- `17012345670000-comparables-land.csv` -- same, for land value per sqft
- `17012345670000-appeal-notes.txt` -- narrative summary explaining why each
  comp is comparable (class, construction, size/age deltas) and what
  evidence backs it, ready to paste into an appeal filing

`output/` contains real property data and is gitignored -- it's never
committed to this repo.

## Why this approach

Cook County's residential appeals are won mainly on the **lack-of-uniformity**
argument: comparable properties (same class, same Assessor neighborhood,
similar size/age/construction) assessed lower per square foot than yours.
3-5 strong comps beats a long list of loosely-similar ones, and a comp that
was *already* reduced on appeal or sold recently below its implied
assessment is much harder for the Board of Review to argue with than a
comp that just happens to have a lower $/sqft.
