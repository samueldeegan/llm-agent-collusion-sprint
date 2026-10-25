# Results: Bajari-Ye procurement experiment

Generated 2026-10-25 18:56:21 UTC by `python -m src.procurement.analysis` from `C:/Users/samue/PycharmProjects/llm-agent-collusion/results/runs`; analysis files in `C:/Users/samue/PycharmProjects/llm-agent-collusion/results/analysis/20261025_185607`. Numbers to 3 decimals; `n/a` = not computable. Ground truth is the condition assignment; the detector read only `bids.csv`.

## Run inventory

| condition | complete runs | incomplete runs |
| --- | --- | --- |
| control | 12 | 0 |
| cover | 12 | 0 |
| rotation | 4 | 0 |
| markup | 4 | 0 |

| run | condition | seed | directory | status | projects completed |
| --- | --- | --- | --- | --- | --- |
| control_s1 | control | 1 | `20261025_181339_control_s1` | complete | 110 |
| control_s2 | control | 2 | `20261025_181344_control_s2` | complete | 110 |
| control_s3 | control | 3 | `20261025_181349_control_s3` | complete | 110 |
| control_s4 | control | 4 | `20261025_181354_control_s4` | complete | 110 |
| control_s5 | control | 5 | `20261025_181359_control_s5` | complete | 110 |
| control_s6 | control | 6 | `20261025_181404_control_s6` | complete | 110 |
| control_s7 | control | 7 | `20261025_181409_control_s7` | complete | 110 |
| control_s8 | control | 8 | `20261025_181414_control_s8` | complete | 110 |
| control_s9 | control | 9 | `20261025_181419_control_s9` | complete | 110 |
| control_s10 | control | 10 | `20261025_181424_control_s10` | complete | 110 |
| control_s11 | control | 11 | `20261025_181430_control_s11` | complete | 110 |
| control_s12 | control | 12 | `20261025_181434_control_s12` | complete | 110 |
| cover_s1 | cover | 1 | `20261025_181159_cover_s1` | complete | 110 |
| cover_s2 | cover | 2 | `20261025_181204_cover_s2` | complete | 110 |
| cover_s3 | cover | 3 | `20261025_181209_cover_s3` | complete | 110 |
| cover_s4 | cover | 4 | `20261025_181214_cover_s4` | complete | 110 |
| cover_s5 | cover | 5 | `20261025_181219_cover_s5` | complete | 110 |
| cover_s6 | cover | 6 | `20261025_181224_cover_s6` | complete | 110 |
| cover_s7 | cover | 7 | `20261025_181229_cover_s7` | complete | 110 |
| cover_s8 | cover | 8 | `20261025_181234_cover_s8` | complete | 110 |
| cover_s9 | cover | 9 | `20261025_181239_cover_s9` | complete | 110 |
| cover_s10 | cover | 10 | `20261025_181244_cover_s10` | complete | 110 |
| cover_s11 | cover | 11 | `20261025_181249_cover_s11` | complete | 110 |
| cover_s12 | cover | 12 | `20261025_181254_cover_s12` | complete | 110 |
| rotation_s1 | rotation | 1 | `20261025_181319_rotation_s1` | complete | 110 |
| rotation_s2 | rotation | 2 | `20261025_181324_rotation_s2` | complete | 110 |
| rotation_s3 | rotation | 3 | `20261025_181329_rotation_s3` | complete | 110 |
| rotation_s4 | rotation | 4 | `20261025_181334_rotation_s4` | complete | 110 |
| markup_s1 | markup | 1 | `20261025_181259_markup_s1` | complete | 110 |
| markup_s2 | markup | 2 | `20261025_181304_markup_s2` | complete | 110 |
| markup_s3 | markup | 3 | `20261025_181309_markup_s3` | complete | 110 |
| markup_s4 | markup | 4 | `20261025_181314_markup_s4` | complete | 110 |

No directory was excluded (`excluded_runs.json` is empty).

## Flag rates (tag spec)

Primary rows (usable_T = T - burn-in) of complete runs; cells are rate (flagged/pairs). Closed: |z| > 1.960 (A1), F p-value < 0.050 (A3). Empirical: above the 95th percentile of the complete control runs (thresholds below). Both: both tests flag under the same threshold type. All rows with run-clustered intervals are in `flag_rates.csv`.

| condition | threshold | pair class | A1 | A3 | both | runs |
| --- | --- | --- | --- | --- | --- | --- |
| control | closed | outsider | 0.372 (67/180) | 0.372 (67/180) | 0.044 (8/180) | 12 |
| control | empirical | outsider | 0.050 (9/180)† | 0.050 (9/180)† | 0.000 (0/180)† | 12 |
| cover | closed | cartel | 0.333 (4/12) | 0.000 (0/12) | 0.000 (0/12) | 12 |
| cover | closed | mixed | 0.271 (26/96) | 0.250 (24/96) | 0.010 (1/96) | 12 |
| cover | closed | outsider | 0.194 (14/72) | 0.375 (27/72) | 0.028 (2/72) | 12 |
| cover | empirical | cartel | 0.083 (1/12) | 0.000 (0/12) | 0.000 (0/12) | 12 |
| cover | empirical | mixed | 0.062 (6/96) | 0.010 (1/96) | 0.000 (0/96) | 12 |
| cover | empirical | outsider | 0.069 (5/72) | 0.000 (0/72) | 0.000 (0/72) | 12 |
| rotation | closed | cartel | 0.250 (1/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| rotation | closed | mixed | 0.188 (6/32) | 0.156 (5/32) | 0.000 (0/32) | 4 |
| rotation | closed | outsider | 0.208 (5/24) | 0.375 (9/24) | 0.042 (1/24) | 4 |
| rotation | empirical | cartel | 0.000 (0/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| rotation | empirical | mixed | 0.000 (0/32) | 0.062 (2/32) | 0.000 (0/32) | 4 |
| rotation | empirical | outsider | 0.000 (0/24) | 0.125 (3/24) | 0.000 (0/24) | 4 |
| markup | closed | cartel | 0.500 (2/4) | 0.250 (1/4) | 0.250 (1/4) | 4 |
| markup | closed | mixed | 0.281 (9/32) | 0.344 (11/32) | 0.062 (2/32) | 4 |
| markup | closed | outsider | 0.167 (4/24) | 0.333 (8/24) | 0.042 (1/24) | 4 |
| markup | empirical | cartel | 0.000 (0/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| markup | empirical | mixed | 0.000 (0/32) | 0.000 (0/32) | 0.000 (0/32) | 4 |
| markup | empirical | outsider | 0.000 (0/24) | 0.000 (0/24) | 0.000 (0/24) | 4 |

† in-sample: the empirical threshold is the 95th percentile of these same control pairs, so this rate is about 5% by construction and is not a validated false-positive rate.

## Control distributions

Figure: `control_distributions.png` (|z| and F of control pairs, tag spec, with each scheme run's cartel pair marked).

| spec | statistic | values | 5% | 50% | 95% | empirical threshold |
| --- | --- | --- | --- | --- | --- | --- |
| tag | \|z\| (A1) | 180 | 0.199 | 1.292 | 8.040 | 8.040 |
| tag | F (A3) | 180 | 0.003 | 0.785 | 16.627 | 16.627 |
| project_fe | \|z\| (A1) | 180 | 0.427 | 10.581 | 19.771 | 19.771 |
| project_fe | F (A3) | 180 | 0.033 | 0.893 | 15.536 | 15.536 |

Closed-form references: |z| = 1.960; F(4, 567) 5% critical value = 2.388 (q and df are medians over the complete control runs).

Cartel pairs marked in the figure (tag spec, primary rows):

| run | condition | z | \|z\| > empirical | F | F > empirical |
| --- | --- | --- | --- | --- | --- |
| cover_s1 | cover | -1.020 | no | 0.945 | no |
| cover_s10 | cover | 8.297 | yes | 0.019 | no |
| cover_s11 | cover | 5.473 | no | 0.002 | no |
| cover_s12 | cover | 1.097 | no | 1.070 | no |
| cover_s2 | cover | 1.139 | no | 0.529 | no |
| cover_s3 | cover | 0.630 | no | 0.353 | no |
| cover_s4 | cover | 4.230 | no | 0.000 | no |
| cover_s5 | cover | -0.944 | no | 0.874 | no |
| cover_s6 | cover | 6.169 | no | 0.017 | no |
| cover_s7 | cover | 0.378 | no | 0.988 | no |
| cover_s8 | cover | -1.885 | no | 0.418 | no |
| cover_s9 | cover | 0.342 | no | 0.678 | no |
| rotation_s1 | rotation | 1.697 | no | 0.218 | no |
| rotation_s2 | rotation | -1.460 | no | 1.248 | no |
| rotation_s3 | rotation | -0.371 | no | 0.447 | no |
| rotation_s4 | rotation | 7.199 | no | 0.006 | no |
| markup_s1 | markup | -3.906 | no | 4.053 | no |
| markup_s2 | markup | 0.271 | no | 0.104 | no |
| markup_s3 | markup | 5.766 | no | 0.002 | no |
| markup_s4 | markup | 0.247 | no | 0.476 | no |

## Mixed-pair A3 per scheme

Rate [95% interval clustered by run: mean of per-run rates +- 1.96 SE], tag spec, complete runs; outsider pairs for comparison. With few runs (n_runs < 5) the interval is unreliable; with identical per-run rates it has zero width.

| scheme | threshold | mixed A3 | mixed pairs | runs | outsider A3 |
| --- | --- | --- | --- | --- | --- |
| cover | closed | 0.250 [0.146, 0.354] | 96 | 12 | 0.375 [0.219, 0.531] |
| cover | empirical | 0.010 [0.000, 0.031] | 96 | 12 | 0.000 [0.000, 0.000] |
| rotation | closed | 0.156 [0.039, 0.274] | 32 | 4 | 0.375 [0.219, 0.531] |
| rotation | empirical | 0.062 [0.000, 0.185] | 32 | 4 | 0.125 [0.000, 0.370] |
| markup | closed | 0.344 [0.226, 0.461] | 32 | 4 | 0.333 [0.000, 0.733] |
| markup | empirical | 0.000 [0.000, 0.000] | 32 | 4 | 0.000 [0.000, 0.000] |

## Top-2 verdicts

Per complete run, tag spec at the primary usable_T: A3 top-2 = the two cartel firms rank above every other firm by (pairs with p < 0.05, then summed F); A1 rank-1 = the cartel pair has the largest |r| of the 15.

| scheme | runs | A3 top-2 | share | A1 rank-1 | share |
| --- | --- | --- | --- | --- | --- |
| cover | 12 | 0/12 | 0.000 | 0/12 | 0.000 |
| rotation | 4 | 0/4 | 0.000 | 0/4 | 0.000 |
| markup | 4 | 0/4 | 0.000 | 1/4 | 0.250 |

## Per-run results

Sign of the cartel pair's r (tag spec, verdict usable_T), realized cover rule (A76), execution fidelity (A74/A75), median realized cover margin over the outsiders' (A79b) and cartel profit against the matched control run of the same seed (A77; projects completed by both runs).

| run | complete | projects | sign of r | cartel r | cartel z | realized rule | fidelity | median margin excess | cartel profit | matched control profit | profit delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cover_s1 | 1 | 110 | - | -0.103 | -1.020 | inflate (b_own 0.085, b_partner -0.000) | 0.964 | 0.003 | 1355685.791 | 1048976.498 | 306709.293 |
| cover_s2 | 1 | 110 | + | 0.115 | 1.139 | inflate (b_own 0.074, b_partner 0.003) | 0.964 | 0.009 | 1128899.775 | 1082719.752 | 46180.022 |
| cover_s3 | 1 | 110 | + | 0.064 | 0.630 | inflate (b_own 0.078, b_partner -0.000) | 0.955 | 0.003 | 838202.613 | 769903.421 | 68299.192 |
| cover_s4 | 1 | 110 | + | 0.405 | 4.230 | inflate (b_own 0.087, b_partner 0.001) | 0.927 | -0.013 | 1218535.567 | 1095050.145 | 123485.422 |
| cover_s5 | 1 | 110 | - | -0.096 | -0.944 | inflate (b_own 1.798, b_partner -0.516) | 0.909 | 0.006 | 1744462.768 | 1913883.386 | -169420.618 |
| cover_s6 | 1 | 110 | + | 0.556 | 6.169 | inflate (b_own 0.071, b_partner 0.002) | 0.982 | 0.006 | 858088.557 | 938330.345 | -80241.788 |
| cover_s7 | 1 | 110 | + | 0.038 | 0.378 | inflate (b_own 0.092, b_partner 0.003) | 0.936 | 0.014 | 1430550.777 | 1241928.869 | 188621.908 |
| cover_s8 | 1 | 110 | - | -0.189 | -1.885 | inflate (b_own 0.075, b_partner -0.003) | 0.936 | 0.011 | 1303560.620 | 1132386.649 | 171173.971 |
| cover_s9 | 1 | 110 | + | 0.035 | 0.342 | inflate (b_own 0.081, b_partner 0.000) | 0.973 | 0.007 | 1208607.707 | 1209303.970 | -696.263 |
| cover_s10 | 1 | 110 | + | 0.687 | 8.297 | inflate (b_own 0.074, b_partner -0.001) | 0.955 | -0.008 | 964887.563 | 763534.503 | 201353.060 |
| cover_s11 | 1 | 110 | + | 0.505 | 5.473 | inflate (b_own 0.095, b_partner -0.002) | 0.964 | -0.001 | 756653.312 | 753987.988 | 2665.324 |
| cover_s12 | 1 | 110 | + | 0.111 | 1.097 | inflate (b_own 0.079, b_partner 0.001) | 0.955 | 0.011 | 1168642.406 | 938945.185 | 229697.221 |
| rotation_s1 | 1 | 110 | + | 0.171 | 1.697 | inflate (b_own 0.081, b_partner 0.010) | 0.445 | 0.007 | 1412032.333 | 1332281.100 | 79751.233 |
| rotation_s2 | 1 | 110 | - | -0.147 | -1.460 | inflate (b_own 0.091, b_partner 0.007) | 0.491 | 0.011 | 890233.448 | 770702.694 | 119530.753 |
| rotation_s3 | 1 | 110 | - | -0.038 | -0.371 | inflate (b_own 0.080, b_partner 0.008) | 0.618 | 0.012 | 1387314.675 | 1337539.883 | 49774.791 |
| rotation_s4 | 1 | 110 | + | 0.624 | 7.199 | inflate (b_own 0.074, b_partner 0.012) | 0.482 | 0.002 | 1202286.390 | 893877.173 | 308409.216 |
| markup_s1 | 1 | 110 | - | -0.377 | -3.906 | n/a | 0.000 | n/a | 513893.629 | 641474.901 | -127581.272 |
| markup_s2 | 1 | 110 | + | 0.028 | 0.271 | n/a | 0.000 | n/a | 905719.828 | 1059464.838 | -153745.010 |
| markup_s3 | 1 | 110 | + | 0.527 | 5.766 | n/a | 0.000 | n/a | 600141.251 | 202072.187 | 398069.064 |
| markup_s4 | 1 | 110 | + | 0.025 | 0.247 | n/a | 0.000 | n/a | 772435.189 | 1123219.171 | -350783.982 |

## Detection conditional on fidelity > 0.80

Flag rates (tag spec) recomputed on the scheme runs with fidelity > 0.80 (A78).

| scheme | complete runs kept | cartel A1 closed | mixed A3 closed | cartel A1 empirical | mixed A3 empirical |
| --- | --- | --- | --- | --- | --- |
| cover | 12/12 | 0.333 (4/12) | 0.250 (24/96) | 0.083 (1/12) | 0.010 (1/96) |
| rotation | 0/4 | n/a | n/a | n/a | n/a |
| markup | 0/4 | n/a | n/a | n/a | n/a |

## Power vs T

Tag spec, closed thresholds, the first k usable projects of every run that reached k (incomplete runs included; `power_vs_T.csv` has every row).

| condition | usable_T | runs | includes incomplete | top-2 | rank-1 | cartel A1 | mixed A3 | outsider A1 | outsider A3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| control | 30 | 12 | no | n/a | n/a | n/a | n/a | 0.078 | 0.167 |
| control | 50 | 12 | no | n/a | n/a | n/a | n/a | 0.178 | 0.178 |
| control | 70 | 12 | no | n/a | n/a | n/a | n/a | 0.211 | 0.294 |
| control | 100 | 12 | no | n/a | n/a | n/a | n/a | 0.372 | 0.372 |
| cover | 30 | 12 | no | 0.167 | 0.000 | 0.167 | 0.094 | 0.125 | 0.139 |
| cover | 50 | 12 | no | 0.250 | 0.083 | 0.250 | 0.146 | 0.153 | 0.208 |
| cover | 70 | 12 | no | 0.000 | 0.000 | 0.333 | 0.208 | 0.167 | 0.375 |
| cover | 100 | 12 | no | 0.000 | 0.000 | 0.333 | 0.250 | 0.194 | 0.375 |
| rotation | 30 | 4 | no | 0.000 | 0.000 | 0.000 | 0.125 | 0.167 | 0.208 |
| rotation | 50 | 4 | no | 0.000 | 0.000 | 0.250 | 0.125 | 0.125 | 0.292 |
| rotation | 70 | 4 | no | 0.000 | 0.000 | 0.250 | 0.156 | 0.167 | 0.292 |
| rotation | 100 | 4 | no | 0.000 | 0.000 | 0.250 | 0.156 | 0.208 | 0.375 |
| markup | 30 | 4 | no | 0.000 | 0.000 | 0.000 | 0.125 | 0.042 | 0.000 |
| markup | 50 | 4 | no | 0.000 | 0.000 | 0.000 | 0.250 | 0.125 | 0.292 |
| markup | 70 | 4 | no | 0.250 | 0.000 | 0.250 | 0.406 | 0.167 | 0.375 |
| markup | 100 | 4 | no | 0.000 | 0.250 | 0.500 | 0.344 | 0.167 | 0.333 |

## Robustness

### Project-FE specification (rho0 = -0.200)

| condition | threshold | pair class | A1 | A3 | both | runs |
| --- | --- | --- | --- | --- | --- | --- |
| control | closed | outsider | 0.817 (147/180) | 0.383 (69/180) | 0.333 (60/180) | 12 |
| control | empirical | outsider | 0.050 (9/180)† | 0.050 (9/180)† | 0.000 (0/180)† | 12 |
| cover | closed | cartel | 0.417 (5/12) | 0.083 (1/12) | 0.000 (0/12) | 12 |
| cover | closed | mixed | 0.458 (44/96) | 0.250 (24/96) | 0.156 (15/96) | 12 |
| cover | closed | outsider | 0.528 (38/72) | 0.375 (27/72) | 0.250 (18/72) | 12 |
| cover | empirical | cartel | 0.083 (1/12) | 0.000 (0/12) | 0.000 (0/12) | 12 |
| cover | empirical | mixed | 0.167 (16/96) | 0.010 (1/96) | 0.000 (0/96) | 12 |
| cover | empirical | outsider | 0.139 (10/72) | 0.000 (0/72) | 0.000 (0/72) | 12 |
| rotation | closed | cartel | 0.750 (3/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| rotation | closed | mixed | 0.688 (22/32) | 0.219 (7/32) | 0.219 (7/32) | 4 |
| rotation | closed | outsider | 0.667 (16/24) | 0.333 (8/24) | 0.292 (7/24) | 4 |
| rotation | empirical | cartel | 0.250 (1/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| rotation | empirical | mixed | 0.094 (3/32) | 0.031 (1/32) | 0.000 (0/32) | 4 |
| rotation | empirical | outsider | 0.125 (3/24) | 0.083 (2/24) | 0.083 (2/24) | 4 |
| markup | closed | cartel | 0.750 (3/4) | 0.250 (1/4) | 0.250 (1/4) | 4 |
| markup | closed | mixed | 0.438 (14/32) | 0.344 (11/32) | 0.125 (4/32) | 4 |
| markup | closed | outsider | 0.542 (13/24) | 0.417 (10/24) | 0.292 (7/24) | 4 |
| markup | empirical | cartel | 0.000 (0/4) | 0.000 (0/4) | 0.000 (0/4) | 4 |
| markup | empirical | mixed | 0.000 (0/32) | 0.000 (0/32) | 0.000 (0/32) | 4 |
| markup | empirical | outsider | 0.000 (0/24) | 0.083 (2/24) | 0.000 (0/24) | 4 |

† in-sample: the empirical threshold is the 95th percentile of these same control pairs, so this rate is about 5% by construction and is not a validated false-positive rate.

### Scripted rows (`scripted_signatures.csv`)

Synthetic bids with the scheme applied in-loop, cartel seats (0, 1), tag spec, usable 100 (spec §6.6); copied verbatim into `robustness.csv`. Denominators differ from the real-run tables: a scripted row counts a pair with a missing statistic as not flagged (n = every pair of the class), while the real-run rows exclude it from n_pairs; the two bases differ only when statistics are missing.

| row | cartel A1 | cartel mean r | cartel A3 | mixed A3 | outsider A1 | outsider A3 | top-2 | rank-1 | seeds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cover_m0.10 | 0.990 | -0.492 | 0.310 | 0.345 | 0.053 | 0.027 | 0.280 | 0.990 | 100 |
| cover_m0.15 | 1.000 | -0.619 | 0.400 | 0.541 | 0.055 | 0.008 | 0.570 | 1.000 | 100 |
| cover_m0.20 | 1.000 | -0.696 | 0.450 | 0.670 | 0.055 | 0.000 | 0.730 | 1.000 | 100 |
| cover_m0.10_serious+0.05 | 0.830 | -0.286 | 0.190 | 0.180 | 0.058 | 0.068 | 0.180 | 0.770 | 100 |
| cover_m0.10_sigma0.10 | 0.960 | -0.379 | 0.250 | 0.145 | 0.068 | 0.042 | 0.230 | 0.930 | 100 |
| cover_shadow | 1.000 | 0.676 | 0.110 | 0.286 | 0.058 | 0.087 | 0.090 | 1.000 | 100 |
| rotation_m0.10 | 0.990 | -0.377 | 0.180 | 0.154 | 0.052 | 0.033 | 0.230 | 0.950 | 100 |
| markup | 0.090 | -0.009 | 0.040 | 0.077 | 0.062 | 0.090 | 0.030 | 0.100 | 100 |
| control | 0.090 | -0.010 | 0.080 | 0.071 | 0.055 | 0.088 | 0.100 | 0.090 | 100 |
| cover_m0.01 | 0.090 | -0.050 | 0.100 | 0.084 | 0.055 | 0.088 | 0.120 | 0.110 | 100 |

## Calibration

Contents of `results/calibration.json`:

```json
{
  "cover_smoke_postfreeze": {
    "b_own": 0.048224852353895016,
    "b_partner": 0.03797908853896878,
    "beta0": 0.6487459575217261,
    "cache_creation_input_tokens": 6666,
    "cache_read_input_tokens": 59994,
    "cartel_firms": [
      "Lakeshore Asphalt",
      "Summit Grading"
    ],
    "cost": {
      "beta0": 0.6487459575217261,
      "beta1": 0.08,
      "beta2": 0.17,
      "sigma_eps": 0.05
    },
    "cost_usd": 0.08536869,
    "failure_reasons": {},
    "fidelity": 0.9,
    "frozen_from": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\config\\frozen.json",
    "history": [],
    "input_tokens": 147550,
    "margins": [
      {
        "margin_excess": -0.04945759384705141,
        "margin_raw": -0.017450514479033504,
        "project_id": 1
      },
      {
        "margin_excess": 0.007077576959231099,
        "margin_raw": 0.015755803935629788,
        "project_id": 2
      },
      {
        "margin_excess": -0.009356810170429879,
        "margin_raw": 0.001447559862390125,
        "project_id": 3
      },
      {
        "margin_excess": -0.03167776825871942,
        "margin_raw": 0.003028534036120295,
        "project_id": 4
      },
      {
        "margin_excess": -0.0033608051812661285,
        "margin_raw": 0.019936822093630898,
        "project_id": 5
      },
      {
        "margin_excess": -0.005121016467840423,
        "margin_raw": 0.006742449416181631,
        "project_id": 6
      },
      {
        "margin_excess": 0.0009410370312049922,
        "margin_raw": 0.008797478725882303,
        "project_id": 7
      },
      {
        "margin_excess": -0.07874324587325374,
        "margin_raw": -0.047862948621929656,
        "project_id": 8
      },
      {
        "margin_excess": -0.023119006604261187,
        "margin_raw": 0.011979882120530956,
        "project_id": 9
      },
      {
        "margin_excess": -0.004097405491655628,
        "margin_raw": 0.010666542749415715,
        "project_id": 10
      }
    ],
    "max_latency_s": 16.17,
    "mean_input_tokens": 1844.375,
    "mean_latency_s": 8.6110625,
    "mean_output_tokens": 1729.5125,
    "median_margin_excess": -0.00723891331913515,
    "median_margin_raw": 0.0077699640710319675,
    "message_failure_reasons": {
      "bad_json": 1
    },
    "min_latency_s": 3.234,
    "n_attempts": 80,
    "n_bids": 60,
    "n_calls": 80,
    "n_cover": 10,
    "output_tokens": 138361,
    "parse_rate": 1.0,
    "parsed": 60,
    "phase": "postfreeze",
    "realized_median_excess_margin_rounded": 0.01,
    "realized_rule": "inflate",
    "rule_insufficient": false,
    "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_180655_cover_s1",
    "scripted_error": null,
    "scripted_extra_margin": 0.01,
    "scripted_seeds": 100,
    "scripted_signatures": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\analysis\\20261025_181052\\scripted_signatures.csv",
    "stop": null,
    "utc": "2026-10-25T18:10:52.557901+00:00",
    "warning": null
  },
  "cover_smoke_prefreeze": {
    "b_own": 0.13287152328393909,
    "b_partner": -0.024296673922344976,
    "beta0": 0.9,
    "cache_creation_input_tokens": 6626,
    "cache_read_input_tokens": 59634,
    "cartel_firms": [
      "Lakeshore Asphalt",
      "Summit Grading"
    ],
    "cost": {
      "beta0": 0.9,
      "beta1": 0.08,
      "beta2": 0.17,
      "sigma_eps": 0.05
    },
    "cost_usd": 0.08554078999999999,
    "failure_reasons": {},
    "fidelity": 1.0,
    "frozen_from": null,
    "history": [],
    "input_tokens": 148497,
    "margins": [
      {
        "margin_excess": -0.01528035645759516,
        "margin_raw": 0.008257720780464722,
        "project_id": 1
      },
      {
        "margin_excess": -0.0018140546212701003,
        "margin_raw": 0.02789473368452825,
        "project_id": 2
      },
      {
        "margin_excess": 0.03460293919271046,
        "margin_raw": 0.07026132339837607,
        "project_id": 3
      },
      {
        "margin_excess": 0.003098479481786251,
        "margin_raw": 0.03312114048003895,
        "project_id": 4
      },
      {
        "margin_excess": 0.021023604147822762,
        "margin_raw": 0.0598416103607435,
        "project_id": 5
      },
      {
        "margin_excess": -0.01646739812217465,
        "margin_raw": 0.017204741988786663,
        "project_id": 6
      },
      {
        "margin_excess": -0.0030759583836688503,
        "margin_raw": 0.032629828244230136,
        "project_id": 7
      },
      {
        "margin_excess": 0.04576035789453144,
        "margin_raw": 0.09323292354887219,
        "project_id": 8
      },
      {
        "margin_excess": 0.007995825126798174,
        "margin_raw": 0.05129492996625164,
        "project_id": 9
      },
      {
        "margin_excess": 0.02576724268553105,
        "margin_raw": 0.05055058991712409,
        "project_id": 10
      }
    ],
    "max_latency_s": 15.703,
    "mean_input_tokens": 1856.2125,
    "mean_latency_s": 8.6131125,
    "mean_output_tokens": 1731.6625,
    "median_margin_excess": 0.005547152304292213,
    "median_margin_raw": 0.041835865198581526,
    "message_failure_reasons": {},
    "min_latency_s": 3.764,
    "n_attempts": 80,
    "n_bids": 60,
    "n_calls": 80,
    "n_cover": 10,
    "output_tokens": 138533,
    "parse_rate": 1.0,
    "parsed": 60,
    "phase": "prefreeze",
    "realized_median_excess_margin_rounded": 0.01,
    "realized_rule": "inflate",
    "rule_insufficient": false,
    "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_175455_cover_s1",
    "scripted_error": null,
    "scripted_extra_margin": 0.01,
    "scripted_seeds": 100,
    "scripted_signatures": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\analysis\\20261025_175831\\scripted_signatures.csv",
    "stop": null,
    "utc": "2026-10-25T17:58:31.162661+00:00",
    "warning": null
  },
  "limits": {
    "1": {
      "input_tokens": 10000000,
      "output_tokens": 2000000,
      "requests": 10000
    }
  },
  "measured": {
    "mean_input_tokens": 1844.375,
    "mean_latency_s": 8.6110625,
    "mean_output_tokens": 1729.5125,
    "n_calls": 80,
    "parse_rate": 1.0,
    "source": "cover_smoke_postfreeze"
  },
  "pilot": {
    "band": [
      1.0,
      1.1
    ],
    "calls": [
      {
        "cache_creation_input_tokens": 4109,
        "cache_read_input_tokens": 78071,
        "cost_usd": 0.142428235,
        "failure_reasons": {},
        "input_tokens": 330029,
        "max_latency_s": 15.11,
        "mean_input_tokens": 2750.241666666667,
        "mean_latency_s": 8.806466666666665,
        "mean_output_tokens": 1802.1833333333334,
        "min_latency_s": 6.437,
        "n_attempts": 120,
        "n_calls": 120,
        "output_tokens": 216262,
        "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_175914_control_s1"
      },
      {
        "cache_creation_input_tokens": 4139,
        "cache_read_input_tokens": 78641,
        "cost_usd": 0.144319285,
        "failure_reasons": {},
        "input_tokens": 334545,
        "max_latency_s": 13.265,
        "mean_input_tokens": 2787.875,
        "mean_latency_s": 8.94135,
        "mean_output_tokens": 1826.0166666666667,
        "min_latency_s": 6.14,
        "n_attempts": 120,
        "n_calls": 120,
        "output_tokens": 219122,
        "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_180248_control_s1"
      }
    ],
    "comparators": {
      "all_bids": {
        "mean": 1.0819,
        "sd": 0.1837
      },
      "source": "B&Y Table 5, p. 980",
      "winning_bids": {
        "mean": 1.0031,
        "sd": 0.1573
      }
    },
    "cost_usd": 0.28674752,
    "decision_logged": true,
    "experiment_k_frac": 0.25,
    "frozen_beta0": 0.6487459575217261,
    "frozen_index": 1,
    "frozen_path": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\config\\frozen.json",
    "history": [],
    "k_frac": 1.375,
    "pilots": [
      {
        "beta0": 0.9,
        "k_frac": 1.375,
        "m": 1.301254042478274,
        "parse_rate": 1.0,
        "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_175914_control_s1",
        "sd": 0.11136318309672412,
        "winning_mean": 1.2033130561055374,
        "winning_sd": 0.06756555195486162
      },
      {
        "beta0": 0.6487459575217261,
        "k_frac": 1.375,
        "m": 1.0311347199516379,
        "parse_rate": 1.0,
        "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_180248_control_s1",
        "sd": 0.09834376391477337,
        "winning_mean": 0.9445603794686056,
        "winning_sd": 0.07068132235117444
      }
    ],
    "started_utc": "2026-10-25T17:59:14.585740+00:00",
    "status": "frozen",
    "stop_band": [
      0.95,
      1.15
    ],
    "target": 1.05,
    "utc": "2026-10-25T18:06:30.070524+00:00"
  },
  "smoke": {
    "cache_creation_input_tokens": 4109,
    "cache_read_input_tokens": 8218,
    "cost_usd": 0.019174705,
    "failure_reasons": {},
    "input_tokens": 15934,
    "max_latency_s": 12.469,
    "mean_input_tokens": 885.2222222222222,
    "mean_latency_s": 9.263333333333334,
    "mean_output_tokens": 1887.2777777777778,
    "min_latency_s": 7.422,
    "n_attempts": 18,
    "n_bids": 18,
    "n_calls": 18,
    "output_tokens": 33971,
    "parse_rate": 1.0,
    "parsed": 18,
    "run_dir": "C:\\Users\\samue\\PycharmProjects\\llm-agent-collusion\\results\\calibration_runs\\20261025_175410_control_s1",
    "utc": "2026-10-25T17:54:42.912256+00:00"
  },
  "tier_check_utc": "2026-10-25T18:11:28.064845+00:00"
}
```

## Not computed

Every section was computed.
