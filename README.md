# Epsilon-protected output for budgeted open last-mile routing

Repository: [epsilon-protected-open-routing](https://github.com/swx441/epsilon-protected-open-routing), release [manuscript-20261022](https://github.com/swx441/epsilon-protected-open-routing/releases/tag/manuscript-20261022).

Code and result records for the manuscript *A Large Neighborhood Search for the Open Vehicle Routing Problem with Urgency-Weighted Service Latency and Tolerance-Constrained Route Selection by Tardiness and Distance*, submitted to *Mathematics*.

The method in the manuscript is JWL-ALNS. The formal protection tolerance is `0.05`. A run that does not pass `--priority-tolerance` uses this value.

## What is in this repository

- The solver, the controlled-instance generator, the evaluator, and the experiment runners.
- `requirements.txt`, the dependency lock used for the manuscript tables.
- `supplementary/S1_computing_environment.md`, which maps each manuscript table to one file under `records/`.
- The result files named in that supplement, copied without rerunning the experiments.

## What is not in this repository

- The Amazon Last Mile Routing Research Challenge raw data, and the normalized route matrices derived from it. Obtain the evaluation split from the original release and its licence, then build the replay inputs with `prepare_amazon_normalized.py`. This repository does not redistribute those files.
- Third-party solver source. OR-Tools, ALNS, and PyVRP are installed from the versions pinned below.
- OpenStreetMap driving graphs used for the replay distance. Supplementary S1 records the OSMnx version and the snap distances.

## Software

The tables were produced with the packages in `requirements.txt`. Supplementary S1 states which interpreter is known. Python 3.8.0 is the environment that built the driving networks for the distances in Section 6.7. The console record of the controlled comparisons and the Amazon rerun gives the package versions and not the Python version.

```text
numpy==1.24.4
scipy==1.10.1
ortools==9.10.4067
pyvrp==0.6.3
alns==6.0.0
```

## Reproduce a controlled instance

From this directory:

```bash
python run_experiments.py --mode smoke --output-dir records/smoke
```

The manuscript comparison uses `--mode controlled`, seeds 0–19, a budget of 5 seconds, and:

```bash
python run_experiments.py --mode controlled --selection-mode priority_guarded --priority-tolerance 0.05
```

Do not expect a new run to reproduce the published tables bit for bit. Supplementary S1 states that Tables 9, 10, and 12 combine three separate runs, and that Tables 15, 16, Section 6.6, and Table A1 were re-selected at `0.05` from archives saved at `0.06`. `reselect_tolerance.py` repeats that re-selection on the archives in `records/`.

## Table records

Paths below are the ones named in Supplementary S1.

| Manuscript | File |
| --- | --- |
| JWL-ALNS rows of Tables 9, 10, and 12 | `records/table09_12_jwl_alns.csv` |
| Priority-Greedy, Weighted MLP-ILS, RLS | `records/table09_12_same_objective.csv` |
| OR-Tools, native ALNS, nearest neighbour | `records/table09_12_travel_time.csv` |
| Table 7 | `records/table07_tolerance_development.csv` |
| Table 8 | `records/table08_tolerance_confirmation.csv` |
| Table 15 | `records/table17_curvature.csv` |
| Permutation in Section 6.5 | `records/section65_permutation.csv` |
| Table 16 | `records/table18_weights.csv` |
| Table 17 | `records/table19_exact.csv` |
| Travel-time rows of Table 12 | `records/table14_travel_time_search.csv` |
| Section 6.6 and Table A1 | `records/section66_tableA1_scale.csv` |
| Section 6.7 and Table 18 | `records/section67_table20/` |

The archives used for the 0.05 re-selection are the `*_archive.csv` files in `records/`. The other methods in Section 6.7 are in `records/section67_other_methods/`.

## Licence

The source code is released under the MIT License. See `LICENSE`.

The MIT License does not apply to the Amazon Last Mile data, which are not included, or to result files derived from that evaluation set. Those files stay under the licence of the original data. The controlled-instance records were generated for this manuscript and are included so the tables can be checked.
