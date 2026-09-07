# Cook County Tax Appeal Comps

Find Cook County property tax appeal comparables for a given PIN, straight
from the county's own ArcGIS and open-data APIs -- no manual CSV export from
CookViewer required.

Live in DuPage County instead? See the
[DuPage County version](https://github.com/mattjonesorg/dupage-county-tax-appeal-comps).

## Quick start

```
python3 comps.py 16-07-204-019-0000
python3 comps.py 16072040190000 --out-dir output/custom-run
```

Requires `requests`, `pandas`, `reportlab`, and `Pillow`
(`pip install requests pandas reportlab Pillow`).

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
   narrative.
5. Suggests field-by-field answers for Cook County's own online appeal
   filer: a Desired Market Value derived from the strongest comps, which
   "Reason(s) for Appeal" checkboxes to select and draft text for each
   required "Explain ..." box on the Appeal Application page, and which
   specific PINs to search for and add on the Comparables Select page.
6. Builds a PDF of the narrative -- ready for the "Appeal Narrative"
   attachment -- with a Cook County assessor field photo of your property
   and each cited comp (same photos CookViewer links to as "Historical
   Photo"), capped at the filer's own 6-comp limit and compressed to stay
   well under its 10MB attachment size cap.

Pass `--no-enrich` to skip the sales/appeal-history lookups (faster, but
without the extra evidence). Pass `--no-photos` to skip fetching photos
for the PDF (faster; the PDF is still built, just without images).

The suggested Desired Market Value defaults to the *25th percentile* $/sqft
of the strongest comps -- below the median, since a plain median ask tends
to be too generous a starting point. Pass `--target-percentile` (0-100) to
move it: 50 = median (more conservative, hardest to dismiss); lower than 25
pushes further toward the cheapest comps for a more aggressive ask, at the
cost of leaning on fewer, more extreme comps (`--target-percentile 0` uses
the single lowest comp).

### Output

By default, results are written to `output/<assessment year>/` (the year is
read from the property's own assessment data, e.g. `output/2026/`; override
with `--out-dir`). For PIN `16-07-204-019-0000` that's:

- `16072040190000-my-property.csv` -- your property's own data
- `16072040190000-comparables-all.csv` -- every comp found, before filtering
- `16072040190000-comparables-building.csv` -- comps cheaper per sqft of
  building value than yours, sorted strongest-evidence-first
- `16072040190000-comparables-land.csv` -- same, for land value per sqft
- `16072040190000-appeal-notes.txt` -- narrative summary explaining why each
  comp is comparable (class, construction, size/age deltas) and what
  evidence backs it, ready to paste into an appeal filing
- `16072040190000-appeal-form-answers.txt` -- suggested answers for Cook
  County's online appeal filer: Desired Market Value, which Reason(s) for
  Appeal checkboxes to select, draft text for each "Explain ..." box, and
  which PINs to search for and add on the Comparables Select page
- `16072040190000-appeal-narrative.pdf` -- the narrative as a PDF exhibit,
  with a field photo of your property and each cited comp -- upload this
  directly as the "Appeal Narrative" attachment
- `16072040190000-comparable-pins.txt` -- just the PIN, address, and city/
  zip for the top (up to 6) comps -- upload this as the required
  "Comparable Property PIN(s)" attachment

`output/` contains real property data and is gitignored -- it's never
committed to this repo.

## Filing your appeal

Once you have the output files, file at
[propertytaxfilings.cookcountyil.gov](https://propertytaxfilings.cookcountyil.gov/Filing/FilingType/Info/CCAO_APPEAL_RES_2026)
(the URL's `_RES_2026` suffix is tax-year-specific -- if you're filing in a
different year, start from the Assessor's site and look for the current
year's residential appeal filing). The filer is a multi-tab Tyler
Technologies form; here's what each tab needs and where it comes from:

1. **Activity Window / Verify Parcel / Primary PIN / Additional PINs /
   Filer / Property Characteristics** -- your own account, contact, and
   parcel-verification info. Nothing from this tool is needed here; just
   confirm the property details the county already has on file match
   reality (correct this first if they don't -- a "Property Description
   Error" is a different appeal basis than uniformity).
2. **Appeal Application** -- use `*-appeal-form-answers.txt` directly:
   Desired Market Value, which "Reason(s) for Appeal" checkboxes to check,
   and the short text for each "Explain ..." box (that box is capped at 40
   characters -- paste it exactly, don't try to add more).
3. **Comparables Select** -- click "Find Comparables" with the default
   criteria, then check the boxes for the PINs listed in the "Comparables
   Select tab" section of `*-appeal-form-answers.txt` (already in priority
   order) and click "Add Selected Parcel(s)."
4. **Comparables** -- just review; the county's own numbers here should
   match what this tool already computed. Nothing to enter.
5. **Attachments** -- upload `*-appeal-narrative.pdf` as the "Appeal
   Narrative" attachment, and `*-comparable-pins.txt` as the required
   "Comparable Property PIN(s)" attachment.
6. **Submit** -- review everything and file.

## Why this approach

Cook County's residential appeals are won mainly on the **lack-of-uniformity**
argument: comparable properties (same class, same Assessor neighborhood,
similar size/age/construction) assessed lower per square foot than yours.
3-5 strong comps beats a long list of loosely-similar ones, and a comp that
was *already* reduced on appeal or sold recently below its implied
assessment is much harder for the Board of Review to argue with than a
comp that just happens to have a lower $/sqft.
