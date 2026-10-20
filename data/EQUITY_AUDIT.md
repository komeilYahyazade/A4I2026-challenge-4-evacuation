# Equity audit: Lee County (FL, 12071)

**Method.** Race and ethnicity enter here and only here, to audit the output; they are never a ranking input.
The source is ACS `censustract_2020_5yr` (bigquery-public-data), joined back to the recommended tracts by
`geo_id`, and compared with the county and the state. Reproduce with `sql/audit/equity_audit.sql` (command below).
Tract counts: 15 in cohort A, 15 in cohort B, 221 in Lee, 5,160 in Florida.

## Who the ranked list lands on

| Cohort | No-car households covered | Share of Lee's 14,553 | People without a car | Avg % mobile homes | Avg % 65+ | Black | Hispanic | White non-Hispanic | Poverty |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A: `RANK_TRACTS` top 15 (as first shipped) | 2,054 | 14% | 3,649 | 61% | 61% | 2.5% | 5.0% | 90.3% | 15.3% |
| B: top 15 by `households_no_vehicle` | 4,900 | 34% | 11,883 | 16% | 29% | 14.9% | 31.9% | 50.1% | 20.3% |
| Lee County | 14,553 | 100% | 36,832 | 10% | 34% | 7.9% | 21.9% | 66.3% | 11.8% |
| Florida | – | – | – | – | – | 15.2% | 25.8% | 53.4% | 13.3% |

## Reporting bias

Wheelchair access is recorded for pre-landfall shelters within 15 km of each cohort at these rates:

| Near cohort A | Near cohort B | All Lee | Statewide |
|---:|---:|---:|---:|
| 9.0% | 10.6% | 9.9% | 14.4% |

## Answer

**Did the plan land where people cannot leave, or where somebody filled in the form?**

The plan did not follow the paperwork: recording is about 10% near every Lee cohort, because almost nobody filled it in anywhere
in the county.

**But the first ranking missed most of the people it was meant for.** The equal-weight composite of four
z-scores is dominated by `pct_mobile_homes` and `pct_age_65_plus` in Lee. Its top 15 are older mobile-home
communities, 90% white non-Hispanic against 66% for the county, and they hold only 14% of Lee's no-car
households. Tracts with 17–19 no-car households ranked #2–#4. Ranking by the scenario's own measure
(no-car household count) covers 2.4× as many households (34%) and lands on tracts that are 47% Black or Hispanic.

Both needs are real. Mobile homes must evacuate in any hurricane, and age is a causal factor. Race is not the fix;
the fix is choosing the measure that matches the question. Recommendation sent to the agent owner (komeil-d2):
publish two lists, "can't drive" (ranked by no-car household count) and "must leave, may need help" (the
mobile-home, 65+ and disability composite). Alternatively, weight the no-car household count.

## After the fix

The brief now publishes two lists, both ranked by **counts**:
- `who_cannot_leave`: "can't drive", ranked by `households_no_vehicle`.
- `must_leave_may_need_help`: state z-scores of mobile homes, people 65+, and estimated people with a disability.

The old composite is kept only as `ranking="composite_v0"`, for this comparison. The prompt forbids planning with it.

Lee, top 15 per list. These figures come from komeil-d2's live run through `audit_equity`, and they reproduce the data lane's numbers above.

| Ranking | No-car households reached | Mobile homes reached | Residents 65+ reached | White non-Hispanic | Poverty | Wheelchair recorded, nearby shelters |
|---|---:|---:|---:|---:|---:|---:|
| Before: `composite_v0` | 14% | 42% | 10% | 90.3% | 15.3% | 6 of 67 |
| After: `who_cannot_leave` (no-car count) | **34%** | 9% | 7% | 50.1% | 20.3% | 10 of 94 |
| After: `must_leave_may_need_help` | 10% | **46%** | **12%** | 78.7% | 12.0% | 9 of 86 |
| Lee County | 100% | 100% | 100% | 66.3% | 11.8% | – |
| Florida | – | – | – | 53.4% | 13.3% | 253 of 1,763 |

**Reading it.**
- The "can't drive" list now reaches 2.4× as many car-less households.
- The "may need help" list still leans white non-Hispanic. That is because Lee's mobile-home parks and retirement communities do, and we report it rather than hide it.
- **Correction:** the original composite was not *less* poor (15.3% vs 11.8% countywide). The skew was whiter and older, not richer.

To reproduce with the data lane's query, pass any list's top 15 tract ids:

```bash
P=qwiklabs-gcp-03-7a0c015d2e8a
KM=$(grep -E '^PARATRANSIT_PROXY_KM *=' agent/reachability.py | tail -1 | sed -E 's/^[^=]*= *//')
sed -e "s/\${PROJECT}/$P/g" -e "s/\${OPS}/readiness_ops/g" -e "s/\${PROXY_KM}/$KM/g" sql/audit/equity_audit.sql \
  | bq --project_id=$P query --use_legacy_sql=false \
  --parameter='geo_ids:ARRAY<STRING>:["<id1>","<id2>",...]' \
  --parameter='county_fips:STRING:12071' --parameter='label:STRING:after'
```

The "nearby" radius for the reporting-bias column is `PARATRANSIT_PROXY_KM` (15 km today), read from
`agent/reachability.py` so the audit and the agent cannot drift apart.

Check `tracts_requested` against `tracts_matched` on every row before quoting any share. A join that
dropped tract ids (a 2010-vintage id, a lost leading zero) looks exactly like "no data here", and only the
two counts side by side tell them apart.

If the "can't drive" list's top 15 is exactly the top 15 by `households_no_vehicle`, the after row equals
cohort B.
