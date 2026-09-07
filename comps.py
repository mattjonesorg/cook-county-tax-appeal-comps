#!/usr/bin/env python3
"""
Find Cook County property tax appeal comparables for a given PIN, straight
from the county's ArcGIS backend (the same API maps.cookcountyil.gov/cookviewer
uses for its "Comparable Properties" search) -- no browser/CSV export needed.

Usage:
    python3 comps.py 16-07-204-019-0000
    python3 comps.py 16072040190000 --out-dir output/custom-run

Writes results under output/<assessment year>/ by default.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
import requests

MAPSERVER = "https://gis.cookcountyil.gov/traditional/rest/services/CookViewer3Parcels/MapServer/0/query"

# Cook County Open Data (Socrata) datasets used to strengthen comps with
# recent-sale and prior-appeal-outcome evidence.
SALES_DATASET = "https://datacatalog.cookcountyil.gov/resource/wvhk-k5uv.json"
BOR_APPEALS_DATASET = "https://datacatalog.cookcountyil.gov/resource/7pny-nedm.json"

SALE_LOOKBACK_YEARS = 5

# Cook County assesses class 200 (residential) property at 10% of fair market
# value, so a sale price isn't directly comparable to CURRENTVALUE_TOTAL --
# it has to be scaled down first.
RESIDENTIAL_ASSESSMENT_LEVEL = 0.10

SOURCE_FIELDS = [
    "PIN14", "PIN14_dash", "street_address", "city_state_zip", "township_name",
    "tax_municipality_name", "NBHD", "BCLASS", "major_class_description",
    "LANDSF", "BLDGSQFT", "BLDGAGE", "bldg_const_desc",
    "CURRENTVALUE_TOTAL", "CURRENTVALUE_LAND", "CURRENTVALUE_BLDG", "current_value_desc",
]

COMP_FIELDS = [
    "PIN14", "PIN14_dash", "street_address", "city_state_zip",
    "LANDSF", "BLDGSQFT", "BLDGAGE", "bldg_const_desc", "BCLASS",
    "CURRENTVALUE_TOTAL", "CURRENTVALUE_LAND", "CURRENTVALUE_BLDG",
]

# Matches the defaults CookViewer's own "Comparable Property Search" form
# pre-fills when you open it for a parcel.
SQFT_TOLERANCE = 0.10
AGE_TOLERANCE_YEARS = 15
SEARCH_RADIUS_MILES = 0.5


def assessment_year(current_value_desc) -> str:
    """Pull the tax year out of e.g. '2026 Assessor Valuation'; falls back to
    this calendar year if the description is missing or unrecognized."""
    match = re.match(r"(\d{4})", str(current_value_desc))
    return match.group(1) if match else str(pd.Timestamp.now().year)


def normalize_pin(pin: str) -> str:
    digits = re.sub(r"\D", "", pin)
    if len(digits) != 14:
        raise ValueError(f"'{pin}' does not look like a 14-digit PIN")
    return f"{digits[0:2]}-{digits[2:4]}-{digits[4:7]}-{digits[7:10]}-{digits[10:14]}"


def fetch_source_property(pin_dash: str) -> dict:
    resp = requests.get(MAPSERVER, params={
        "f": "json",
        "where": f"PIN14_dash='{pin_dash}'",
        "outFields": ",".join(SOURCE_FIELDS),
        "returnGeometry": "true",
        "outSR": 4326,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Assessor API error: {data['error']}")
    features = data.get("features", [])
    if not features:
        raise ValueError(f"No property found for PIN {pin_dash}")
    feature = features[0]
    return {"attributes": feature["attributes"], "geometry": feature["geometry"]}


def fetch_comparables(source: dict) -> list[dict]:
    attrs = source["attributes"]
    bldg_sqft = attrs["BLDGSQFT"]
    land_sf = attrs["LANDSF"]
    bldg_age = attrs["BLDGAGE"]

    where = (
        f"township_name = '{attrs['township_name']}' "
        f"AND NBHD = {attrs['NBHD']} "
        f"AND BCLASS = '{attrs['BCLASS']}' "
        f"AND PIN14 <> '{attrs['PIN14']}' "
        f"AND (BLDGSQFT >= {bldg_sqft * (1 - SQFT_TOLERANCE):.1f} AND BLDGSQFT <= {bldg_sqft * (1 + SQFT_TOLERANCE):.1f}) "
        f"AND (LANDSF >= {land_sf * (1 - SQFT_TOLERANCE):.0f} AND LANDSF <= {land_sf * (1 + SQFT_TOLERANCE):.0f}) "
        f"AND (BLDGAGE >= {bldg_age - AGE_TOLERANCE_YEARS} AND BLDGAGE <= {bldg_age + AGE_TOLERANCE_YEARS}) "
        f"AND bldg_const_desc = '{attrs['bldg_const_desc']}'"
    )

    resp = requests.get(MAPSERVER, params={
        "f": "json",
        "geometry": json.dumps(source["geometry"]),
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326,
        "outSR": 4326,
        "distance": SEARCH_RADIUS_MILES,
        "units": "esriSRUnit_StatuteMile",
        "spatialRel": "esriSpatialRelIntersects",
        "where": where,
        "outFields": ",".join(COMP_FIELDS),
        "returnGeometry": "false",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Assessor API error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def soql_pin_list(pins: list[str]) -> str:
    return "(" + ",".join(f"'{p}'" for p in pins) + ")"


def fetch_recent_sales(pins: list[str]) -> pd.DataFrame:
    """Most recent arm's-length sale per PIN, from Assessor - Parcel Sales."""
    if not pins:
        return pd.DataFrame(columns=["PIN14", "last_sale_date", "last_sale_price"])
    cutoff_year = pd.Timestamp.now().year - SALE_LOOKBACK_YEARS
    resp = requests.get(SALES_DATASET, params={
        "$where": (
            f"pin in {soql_pin_list(pins)} "
            f"AND year >= {cutoff_year} "
            "AND sale_filter_less_than_10k = false "
            "AND sale_filter_deed_type = false"
        ),
        "$order": "sale_date DESC",
        "$limit": 5000,
    }, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame(columns=["PIN14", "last_sale_date", "last_sale_price"])
    df = pd.DataFrame(rows)
    df["sale_date"] = pd.to_datetime(df["sale_date"])
    df["sale_price"] = pd.to_numeric(df["sale_price"], errors="coerce")
    latest = df.sort_values("sale_date", ascending=False).groupby("pin", as_index=False).first()
    return latest.rename(columns={
        "pin": "PIN14", "sale_date": "last_sale_date", "sale_price": "last_sale_price",
    })[["PIN14", "last_sale_date", "last_sale_price"]]


def fetch_appeal_history(pins: list[str]) -> pd.DataFrame:
    """Most recent Board of Review outcome per PIN, plus whether it was ever reduced."""
    empty = pd.DataFrame(columns=[
        "PIN14", "last_appeal_year", "last_appeal_result", "ever_reduced_at_bor",
    ])
    if not pins:
        return empty
    resp = requests.get(BOR_APPEALS_DATASET, params={
        "$where": f"pin in {soql_pin_list(pins)}",
        "$order": "tax_year DESC",
        "$limit": 5000,
    }, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return empty
    df = pd.DataFrame(rows)
    df["tax_year"] = pd.to_numeric(df["tax_year"], errors="coerce")
    df["assessor_totalvalue"] = pd.to_numeric(df["assessor_totalvalue"], errors="coerce")
    df["bor_totalvalue"] = pd.to_numeric(df["bor_totalvalue"], errors="coerce")
    df["was_reduced"] = df["bor_totalvalue"] < df["assessor_totalvalue"]
    df = df.sort_values("tax_year", ascending=False)
    summary = df.groupby("pin").agg(
        last_appeal_year=("tax_year", "first"),
        last_appeal_result=("result", "first"),
        ever_reduced_at_bor=("was_reduced", "any"),
    ).reset_index()
    return summary.rename(columns={"pin": "PIN14"})


def add_per_sqft_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["building_value_per_square_foot"] = df["CURRENTVALUE_BLDG"] / df["BLDGSQFT"]
    df["land_value_per_square_foot"] = df["CURRENTVALUE_LAND"] / df["LANDSF"]
    df["url"] = "https://www.cookcountyassessor.com/pin/" + df["PIN14"]
    return df


def add_evidence_score(df: pd.DataFrame) -> pd.DataFrame:
    """Rank comps by how well they'd hold up at the Board of Review: a comp that
    was already reduced on appeal, or whose current assessment exceeds what its
    own recent sale implies (at the 10% residential assessment level), is
    stronger evidence than a same-class parcel with no track record."""
    df = df.copy()
    implied_assessed_from_sale = df.get("last_sale_price", pd.Series(dtype=float)) * RESIDENTIAL_ASSESSMENT_LEVEL
    df["evidence_score"] = (
        df.get("ever_reduced_at_bor", False).fillna(False).astype(int) * 2
        + (df["CURRENTVALUE_TOTAL"] > implied_assessed_from_sale).fillna(False).astype(int)
    )
    return df


def _compare(value, reference, unit, higher_word, lower_word) -> str:
    diff = value - reference
    if diff == 0:
        return f"{value:,.0f} {unit} (same as mine)"
    word = higher_word if diff > 0 else lower_word
    return f"{value:,.0f} {unit} ({abs(diff):,.0f} {unit} {word})"


def similarity_phrase(comp: pd.Series, mine: pd.Series) -> str:
    bldg = _compare(comp["BLDGSQFT"], mine["BLDGSQFT"], "sqft building", "bigger", "smaller")
    land = _compare(comp["LANDSF"], mine["LANDSF"], "sqft lot", "bigger", "smaller")
    age = _compare(comp["BLDGAGE"], mine["BLDGAGE"], "yrs", "older", "newer")
    return f"{bldg}, {land}, {age}, same class ({comp['BCLASS']}) and {comp['bldg_const_desc'].lower()} construction"


def format_comp_line(row: pd.Series, my_property: pd.Series) -> str:
    tags = []
    if row.get("ever_reduced_at_bor") is True:
        tags.append(f"reduced on appeal in {int(row['last_appeal_year'])}")
    implied_assessed = row.get("last_sale_price")
    if pd.notna(implied_assessed):
        implied_assessed = implied_assessed * RESIDENTIAL_ASSESSMENT_LEVEL
        if row["CURRENTVALUE_TOTAL"] > implied_assessed:
            sold = row["last_sale_date"]
            sold_str = sold.strftime("%b %Y") if pd.notna(sold) else "recently"
            tags.append(
                f"sold {sold_str} for ${row['last_sale_price']:,.0f}, which at Cook "
                f"County's {RESIDENTIAL_ASSESSMENT_LEVEL:.0%} assessment level implies "
                f"a ${implied_assessed:,.0f} assessed value -- yet it's actually "
                f"assessed at ${row['CURRENTVALUE_TOTAL']:,.0f}"
            )
    tag_str = f" [{'; '.join(tags)}]" if tags else ""
    return (
        f"* {row['PIN14_dash']}\t{row['street_address']}\n"
        f"    ${row['building_value_per_square_foot']:.2f}/sqft building value "
        f"(mine: ${my_property['building_value_per_square_foot']:.2f}/sqft) -- "
        f"{similarity_phrase(row, my_property)}.{tag_str}"
    )


def build_narrative(my_property: pd.Series, cheaper_comps: pd.DataFrame) -> str:
    lines = [
        f"My property ({my_property['PIN14_dash']}, {my_property['street_address']}) is a "
        f"class {my_property['BCLASS']} {my_property['bldg_const_desc'].lower()} home, "
        f"{my_property['BLDGSQFT']:,.0f} sqft on a {my_property['LANDSF']:,.0f} sqft lot, "
        f"{my_property['BLDGAGE']:.0f} years old, currently assessed at "
        f"${my_property['building_value_per_square_foot']:.2f} per square foot of building value.",
        "",
    ]
    if my_property.get("last_appeal_year") is not None and pd.notna(my_property.get("last_appeal_year")):
        lines.append(
            f"(For reference: my own last Board of Review appeal was in "
            f"{int(my_property['last_appeal_year'])}, result: {my_property['last_appeal_result']}.)"
        )
        lines.append("")
    lines += [
        "The following properties are genuinely comparable -- same property class, "
        "construction type, township, and Assessor neighborhood code as mine, within "
        f"{SEARCH_RADIUS_MILES} miles, and within 10% of my building/land square footage "
        "and 15 years of my building's age (each comp's exact size/age difference from "
        "mine is noted below so it's clear how close a match it is) -- yet are assessed "
        "at a lower building value per square foot. Listed strongest evidence first (a "
        "prior appeal reduction or a recent below-assessment sale outweighs a raw $/sqft "
        "gap alone):",
        "",
    ]
    for _, row in cheaper_comps.iterrows():
        lines.append(format_comp_line(row, my_property))
    return "\n".join(lines)


def cite_comps(df: pd.DataFrame, value_col: str, n: int = 3) -> str:
    cites = [
        f"{row['PIN14_dash']} ({row['street_address']}, ${row[value_col]:.2f}/sqft)"
        for _, row in df.head(n).iterrows()
    ]
    return "; ".join(cites)


def suggest_target_values(my_property: pd.Series, building_comps: pd.DataFrame,
                           land_comps: pd.DataFrame, top_n: int = 5,
                           percentile: float = 50) -> dict:
    """A defensible starting point for the form's 'Desired Market Value' field:
    a percentile of the strongest comps' $/sqft (50 = median; lower means a
    more aggressive ask, at the cost of leaning on fewer/more extreme comps),
    applied to this property's own square footage, scaled back up to market
    value."""
    quantile = percentile / 100
    if not building_comps.empty:
        target_building_psf = building_comps.head(top_n)["building_value_per_square_foot"].quantile(quantile)
        target_building_value = target_building_psf * my_property["BLDGSQFT"]
    else:
        target_building_value = my_property["CURRENTVALUE_BLDG"]
    if not land_comps.empty:
        target_land_psf = land_comps.head(top_n)["land_value_per_square_foot"].quantile(quantile)
        target_land_value = target_land_psf * my_property["LANDSF"]
    else:
        target_land_value = my_property["CURRENTVALUE_LAND"]
    target_assessed_total = target_building_value + target_land_value
    return {
        "target_building_value": target_building_value,
        "target_land_value": target_land_value,
        "target_assessed_total": target_assessed_total,
        "desired_market_value": target_assessed_total / RESIDENTIAL_ASSESSMENT_LEVEL,
        "current_market_value": my_property["CURRENTVALUE_TOTAL"] / RESIDENTIAL_ASSESSMENT_LEVEL,
    }


def has_sale_evidence(comps: pd.DataFrame) -> bool:
    if comps.empty or "last_sale_price" not in comps.columns:
        return False
    implied_assessed = comps["last_sale_price"] * RESIDENTIAL_ASSESSMENT_LEVEL
    return bool(((comps["CURRENTVALUE_TOTAL"] > implied_assessed) & comps["last_sale_price"].notna()).any())


# Cook County's own online filer truncates the "Explain '<reason>'" boxes
# after 40 characters (confirmed by pasting a longer explanation and seeing
# it cut off mid-word) -- nowhere near enough room to cite specific PINs, so
# these stay short and generic. The actual PIN-level evidence belongs in the
# Comparables tab and the attached CSVs, not this box.
FORM_TEXT_FIELD_MAX_CHARS = 40
UNIFORMITY_EXPLANATION = "Similar homes assessed lower per sqft"
OVERVALUATION_EXPLANATION = "Comparable sale below assessed value"
assert len(UNIFORMITY_EXPLANATION) <= FORM_TEXT_FIELD_MAX_CHARS
assert len(OVERVALUATION_EXPLANATION) <= FORM_TEXT_FIELD_MAX_CHARS


def build_form_answers(my_property: pd.Series, building_comps: pd.DataFrame,
                        land_comps: pd.DataFrame, top_n: int = 5,
                        percentile: float = 50) -> str:
    """Field-by-field answers for Cook County's online 'Appeal Application'
    page (the one with Fair/Desired Market Value, Reason(s) for Appeal
    checkboxes, and an 'Explain ...' box for each checked reason)."""
    if building_comps.empty and land_comps.empty:
        return (
            "No comparable properties came back cheaper than this one on either "
            "building or land value per square foot -- there's no uniformity case "
            "to make from this data alone this year. Consider widening the search "
            "(a larger radius or age/sqft tolerance) before filing, or skip this "
            "appeal cycle."
        )

    values = suggest_target_values(my_property, building_comps, land_comps, top_n, percentile)
    sale_evidence = has_sale_evidence(building_comps) or has_sale_evidence(land_comps)
    percentile_label = "median" if percentile == 50 else f"{percentile:g}th percentile"

    lines = [
        "=== Cook County online Appeal Application: suggested answers ===",
        "",
        f"Fair Market Value (shown by the county, read-only): ${values['current_market_value']:,.0f}",
        "",
        f"Desired Market Value: ${values['desired_market_value']:,.0f}",
        (
            f"  ({percentile_label} $/sqft of the {min(top_n, len(building_comps))} strongest "
            f"building comps and {min(top_n, len(land_comps))} strongest land comps below, "
            f"applied to this property's own {my_property['BLDGSQFT']:,.0f} sqft building / "
            f"{my_property['LANDSF']:,.0f} sqft lot, then scaled to market value at Cook "
            f"County's {RESIDENTIAL_ASSESSMENT_LEVEL:.0%} residential assessment level. "
            "This is a defensible starting point, not a legal requirement -- round it to "
            "a number you're comfortable arguing for. Lower percentiles push closer to "
            "the cheapest comps: a more aggressive ask, but easier to dismiss if that "
            "comp is an outlier. --target-percentile controls this.)"
        ),
        "",
        "Reason(s) for Appeal: check \"Lack of Uniformity/Comparables\""
        + (" and \"Overvaluation\"" if sale_evidence else ""),
        "",
    ]

    lines.append(
        f"Explain 'Lack of Uniformity/Comparables' (max {FORM_TEXT_FIELD_MAX_CHARS} chars, "
        "paste exactly):"
    )
    lines.append(f'  "{UNIFORMITY_EXPLANATION}"')
    lines.append("")

    if sale_evidence:
        lines.append(
            f"Explain 'Overvaluation' (max {FORM_TEXT_FIELD_MAX_CHARS} chars, paste exactly):"
        )
        lines.append(f'  "{OVERVALUATION_EXPLANATION}"')
        lines.append("")

    if not building_comps.empty:
        cites = cite_comps(building_comps, "building_value_per_square_foot")
    else:
        cites = cite_comps(land_comps, "land_value_per_square_foot")
    lines.append(
        "The county's explain boxes are too short for specifics -- put the actual "
        "evidence in the Comparables tab and/or as an attached PDF/CSV instead. For "
        f"reference, the strongest comps are: {cites}."
    )
    lines.append("")

    lines.append(
        f"How is the Subject Property used?: Single Family (property class "
        f"{my_property['BCLASS']} is single-family residential)"
    )
    lines.append(
        "Field Check Request: No, unless you're disputing your own property's "
        "characteristics (sqft/age/condition) rather than its value"
    )
    lines.append("")
    lines.append(build_comparables_tab_guidance(building_comps, top_n))
    return "\n".join(lines)


def build_comparables_tab_guidance(building_comps: pd.DataFrame, top_n: int = 5) -> str:
    """Guidance for the 'Comparables Select' / 'Comparables' tabs, which come
    after the Appeal Application page and only appear if 'Lack of
    Uniformity/Comparables' was checked there."""
    lines = [
        "=== Comparables Select tab ===",
        "",
        "The county's own default search criteria here (neighborhood, class, "
        "year built range, living area range) already match this tool's filters "
        "-- click \"Find Comparables\" as-is. This tab's standard criteria don't "
        "include a distance limit by default, but distance still matters for a "
        "credible case (the Board of Review's own guidance favors comps on your "
        "block or within a block or two): either add a Distance (ft.) value under "
        "\"Custom / Additional Search Criteria\" (this tool's default is 0.5 mi "
        "= 2,640 ft), or just select from the ranked list below, which is already "
        "distance-filtered.",
        "",
        "In the Search Results grid, check the box for each PIN below (already "
        "in priority order) and click \"Add Selected Parcel(s)\". 3-5 total is "
        "plenty -- adding every match doesn't strengthen the case:",
        "",
    ]
    if building_comps.empty:
        lines.append("(No comps to suggest -- see the note above.)")
        return "\n".join(lines)
    for i, (_, row) in enumerate(building_comps.head(top_n).iterrows(), start=1):
        tag = " -- reduced on appeal" if row.get("ever_reduced_at_bor") is True else ""
        lines.append(f"{i}. {row['PIN14_dash']} ({row['street_address']}){tag}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pin", help="Property Index Number (PIN10/PIN14, with or without dashes)")
    parser.add_argument("--out-dir", default=None,
                         help="Output folder (default: output/<assessment year>)")
    parser.add_argument("--no-enrich", action="store_true",
                         help="Skip the Parcel Sales / Board of Review lookups (faster, less evidence)")
    parser.add_argument("--target-percentile", type=float, default=50,
                         help="Percentile (0-100) of the strongest comps' $/sqft to use for "
                              "the suggested Desired Market Value. 50 = median (default, most "
                              "defensible); lower is a more aggressive ask but leans on fewer, "
                              "more extreme comps; 0 = the single lowest comp.")
    args = parser.parse_args()
    if not 0 <= args.target_percentile <= 100:
        parser.error("--target-percentile must be between 0 and 100")

    pin_dash = normalize_pin(args.pin)
    print(f"Looking up {pin_dash}...")
    source = fetch_source_property(pin_dash)
    my_property = pd.DataFrame([source["attributes"]])
    my_property = add_per_sqft_columns(my_property)

    print("Searching for comparable properties...")
    comps_raw = fetch_comparables(source)
    if not comps_raw:
        print("No comparable properties found with the default filters.", file=sys.stderr)
        sys.exit(1)
    comparables = add_per_sqft_columns(pd.DataFrame(comps_raw))

    if not args.no_enrich:
        all_pins = comparables["PIN14"].tolist() + [source["attributes"]["PIN14"]]
        try:
            print("Checking recent sales (Assessor - Parcel Sales)...")
            sales = fetch_recent_sales(all_pins)
        except requests.RequestException as e:
            print(f"  warning: sales lookup failed ({e}), continuing without it", file=sys.stderr)
            sales = pd.DataFrame(columns=["PIN14", "last_sale_date", "last_sale_price"])
        try:
            print("Checking prior appeal outcomes (Board of Review Appeal Decision History)...")
            appeals = fetch_appeal_history(all_pins)
        except requests.RequestException as e:
            print(f"  warning: appeal history lookup failed ({e}), continuing without it", file=sys.stderr)
            appeals = pd.DataFrame(columns=["PIN14", "last_appeal_year", "last_appeal_result", "ever_reduced_at_bor"])

        comparables = comparables.merge(sales, on="PIN14", how="left").merge(appeals, on="PIN14", how="left")
        comparables = add_evidence_score(comparables)
        my_property = my_property.merge(sales, on="PIN14", how="left").merge(appeals, on="PIN14", how="left")

    my_bldg_psf = my_property["building_value_per_square_foot"].iloc[0]
    my_land_psf = my_property["land_value_per_square_foot"].iloc[0]

    sort_cols = ["evidence_score", "building_value_per_square_foot"] if "evidence_score" in comparables.columns else ["building_value_per_square_foot"]
    sort_asc = [False, True] if len(sort_cols) == 2 else [True]
    building_comps = comparables[comparables["building_value_per_square_foot"] < my_bldg_psf] \
        .sort_values(sort_cols, ascending=sort_asc).reset_index(drop=True)
    land_comps = comparables[comparables["land_value_per_square_foot"] < my_land_psf] \
        .sort_values("land_value_per_square_foot").reset_index(drop=True)

    year = assessment_year(source["attributes"].get("current_value_desc"))
    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / year
    out_dir.mkdir(parents=True, exist_ok=True)
    pin_slug = pin_dash.replace("-", "")

    my_property.to_csv(out_dir / f"{pin_slug}-my-property.csv", index=False)
    comparables.to_csv(out_dir / f"{pin_slug}-comparables-all.csv", index=False)
    building_comps.to_csv(out_dir / f"{pin_slug}-comparables-building.csv", index=False)
    land_comps.to_csv(out_dir / f"{pin_slug}-comparables-land.csv", index=False)

    narrative = build_narrative(my_property.iloc[0], building_comps)
    narrative_path = out_dir / f"{pin_slug}-appeal-notes.txt"
    narrative_path.write_text(narrative + "\n")

    form_answers = build_form_answers(my_property.iloc[0], building_comps, land_comps,
                                       percentile=args.target_percentile)
    form_answers_path = out_dir / f"{pin_slug}-appeal-form-answers.txt"
    form_answers_path.write_text(form_answers + "\n")

    print(f"\n{len(comparables)} comparable properties found within {SEARCH_RADIUS_MILES} mi "
          f"(same class {source['attributes']['BCLASS']}, township, neighborhood, construction).")
    print(f"{len(building_comps)} are cheaper per sq ft of building value than "
          f"${my_bldg_psf:.2f}/sqft.")
    print(f"\nWrote:\n  {out_dir / f'{pin_slug}-my-property.csv'}\n"
          f"  {out_dir / f'{pin_slug}-comparables-all.csv'}\n"
          f"  {out_dir / f'{pin_slug}-comparables-building.csv'}\n"
          f"  {out_dir / f'{pin_slug}-comparables-land.csv'}\n"
          f"  {narrative_path}\n"
          f"  {form_answers_path}")
    print(f"\n{form_answers}")
    print(f"\n{narrative}")


if __name__ == "__main__":
    main()
