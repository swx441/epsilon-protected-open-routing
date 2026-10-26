# Supplementary Material S1. Computing Environment, Reproduction Record, and Median Tables

This file accompanies the manuscript "A Large Neighborhood Search for the Open Vehicle Routing Problem with Urgency-Weighted Service Latency and Tolerance-Constrained Route Selection by Tardiness and Distance". It records the software versions, seeds, budgets, and host configuration of the formal comparison, and it reports the medians and interquartile ranges that the manuscript summarizes in Section 6.2.

## S1.1. Interpreter and Solvers

The tables in the manuscript come from the dependency lock `requirements.txt`. The versions below are reported only when confirmed by a record, and the stages are not given one shared Python version.

The road-network preprocessing is the one stage tied to a recorded interpreter. The virtual environment that holds OSMnx 1.9.4, and that built the driving networks used for the distances in Section 6.7, uses Python 3.8.0, as recorded in its `pyvenv.cfg`. That environment does not contain OR-Tools, PyVRP, or ALNS, so it did not run the comparisons.

The interpreter that holds the pinned packages of the lock file, and that produced the Amazon rerun in Section 6.7 and the controlled comparisons, is not identified by the console record of those runs. The record gives the package versions and not the Python minor version, so none is stated here. The compiled bytecode left in the repository was produced by more than one Python version and is not used to assign a version to a run.

| Component | Formal comparison, `requirements.txt` |
| --- | --- |
| Python | Not recorded in the run logs |
| NumPy | 1.24.4 |
| SciPy | 1.10.1 |
| OR-Tools | 9.10.4067 |
| PyVRP | 0.6.3 |
| ALNS | 6.0.0 |

PyVRP is listed only as a version record. Section 5.2 of the manuscript explains why the urgency-ignoring hard time-window PyVRP-HGS is not included in Tables 9 and 10.

The implementation languages differ. The vehicle-routing search kernel of OR-Tools 9.10.4067 is C++. The method of this paper, Weighted MLP-ILS, RLS, Priority-Greedy, and native ALNS run in Python; the ALNS package provides only a Python interface. Section 5.4 of the manuscript therefore treats the budget as the search time inside one process, not as an equal number of iterations per second.

## S1.2. Host Configuration

The tables were computed on one cloud host. The table below records only what the host console reports. The console does not give the physical CPU model, and this file does not supply one.

| Item | Configuration |
| --- | --- |
| Region | Anhui |
| Compute | 1 GPU, L40S, 48 GB memory |
| CPU | 24 vCPUs; the physical CPU model is not given by the console |
| Memory | 120 GB |
| System disk | 30 GB |
| Data disk | 50 GB base; the console also shows an expandable data disk, and the expansion used here was 0 GB |
| Image | Development environment, PyTorch 2.5.1, CUDA 12.4.1 (image label V2.5.1, about 18 GB, image time 2025-03-28 00:40:13) |

The search timing of the formal comparison is CPU wall-clock time and does not use the GPU. The PyTorch and CUDA in the image are not used in the solve in Section 5 and do not change the values in Table 9.

## S1.3. Random Seeds and Budgets

| Experiment | Seeds | CPU wall-clock budget per instance |
| --- | --- | --- |
| Tolerance development scan | 20–39 | The controlled-instance budget of the main experiment |
| Tolerance confirmation | 60–79 | The same |
| 24-node main experiment | 20 paired seeds in each of three scenarios | 5 s |
| Small-scale exact reference | 8, 12, and 16 nodes, 270 instances | The exact dynamic program has no time limit; the heuristic budget in the same comparison is 0.20 s |
| Scale experiment | 24, 48, 80, and 120 nodes | 1, 5, 10, and 30 s |
| Amazon replay | 100 routes, five seeds each, 500 paired records | 30 s |

The formal protection tolerance is 0.05. It was chosen once on the development set, and the independent seeds are used only to confirm the direction of the effect. Timing starts when the search starts. Failed and timed-out records keep their original status.

## S1.4. Source of the Tables in the Manuscript

The tables are not the output of one run, except Section 6.7 and Table 15, whose source is given below. In Tables 9 and 10 and in Supplementary Tables S2, S3, and S4, the JWL-ALNS row, the three same-objective heuristics, and OR-Tools, native ALNS, and Nearest-Neighbor come from three separate runs, using the same evaluator, the same scenario definitions, and the same wall-clock budget. The tolerance scan, the exact reference, and the attribution were rerun after the tolerance was changed to 0.05. The search archives of the curvature, the weights, and the scale experiment were saved at tolerance 0.06; the values in Supplementary Tables S6 and S7, Table 13, Section 6.6, and Table A1 were re-selected from those archives at $\varepsilon=0.05$, without rerunning the search. The permutation experiment saved no archive and was rerun at $\varepsilon=0.05$. The table below identifies the source; it does not change the values in the manuscript. Paths are relative to the repository root.

| Manuscript | Record file | Seeds and budget |
| --- | --- | --- |
| The JWL-ALNS row of Table 9 and Supplementary Table S2, and the JWL-ALNS row of each scenario in Table 10 and Supplementary Tables S3 and S4; the two JWL-guided rows of Table 11 | `records/table09_12_jwl_alns.csv`, the rows at $\varepsilon=0.05$ | 20 seeds in each of three scenarios, 5 s |
| Priority-Greedy, Weighted MLP-ILS, and RLS in Tables 9 and 10 and in Supplementary Tables S2, S3, and S4 | `records/table09_12_same_objective.csv` | The same scenarios and seeds as the main experiment, 5 s |
| OR-Tools, native ALNS, and Nearest-Neighbor in Tables 9 and 10 and in Supplementary Tables S2, S3, and S4 | `records/table09_12_travel_time.csv` | The same scenarios and seeds as the main experiment, 5 s |
| Table 7 | `records/table07_tolerance_development.csv` | Development seeds 20–39 |
| Table 8 | `records/table08_tolerance_confirmation.csv`, the rows at $\varepsilon=0.05$ | Confirmation seeds 60–79 |
| Supplementary Table S5 | `records/table09_12_jwl_alns.csv`, `records/table09_12_same_objective.csv`, and `records/table09_12_travel_time.csv` | The paired comparisons of tardiness and distance on the same 60 instances as Table 12, 5 s |
| Supplementary Table S6 | `records/table17_curvature.csv`, re-selected at $\varepsilon=0.05$ from `records/table17_curvature_archive.csv` | Four curvatures, five seeds in each of three scenarios per curvature, 5 s |
| Permutation in Section 6.5 | `records/section65_permutation.csv` | 60 controlled instances, 5 s, $\varepsilon=0.05$ |
| Table 13 and Supplementary Table S7 | `records/table18_weights.csv`, re-selected at $\varepsilon=0.05$ from `records/table18_weights_archive.csv` | Four weight settings, 60 instances each, 5 s |
| Table 14 | `records/table19_exact.csv` | 8, 12, and 16 nodes, 270 instances; the exact dynamic program has no time limit and the heuristic budget is 0.20 s |
| The two travel-time rows of Table 11 | `records/table14_travel_time_search.csv` | 60 controlled instances, 5 s |
| Section 6.6 and Table A1 | `records/section66_tableA1_scale.csv`, re-selected at $\varepsilon=0.05$ from `records/section66_tableA1_scale_archive.csv` | Four sizes, five seeds in each of three scenarios, 1, 5, 10, and 30 s |
| Section 6.7 and Table 15 | `records/section67_table20/jwl_alns_shard0.csv` to `jwl_alns_shard5.csv`, with the accepted archive in `jwl_alns_archive.csv` | 100 routes, five seeds each, 30 s, $\varepsilon=0.05$ |

The methods other than JWL-ALNS in Section 6.7 come from `records/section67_other_methods/shard0.csv` to `shard5.csv`. They do not use the protection tolerance, so they were not rerun. JWL-ALNS and its priority-only output were rerun at $\varepsilon=0.05$ and selected by Equations (18) and (19) directly. The accepted nondominated archive of each search is saved, and no record was replaced by a fallback.

Distance is the shortest path on the OpenStreetMap driving network, downloaded through OSMnx 1.9.4 from the Overpass API and saved as one graph per metropolitan area: Boston (134,887 nodes), Seattle (121,766), Los Angeles (315,749), Austin (15,941), and Chicago (160,888). The download date is not in the build log, so it cannot be stated. Each stop was snapped to the nearest network node. Across the 100 routes the median snap distance is 47 m, the 90th percentile is 63 m, and the maximum is 156 m. For node pairs left unconnected by the driving network, the distance was filled with the great-circle distance multiplied by 1.4. This fills 635 of the 466,008 off-diagonal pairs, on five routes: `RouteID_c28cddb1` (52 pairs), `RouteID_f31379fd` (79), `RouteID_fcddcd15` (76), `RouteID_5fba6b43` (148), and `RouteID_7830df77` (280). Travel time stays the published matrix. Section 6.7 reports the distance result again with these five routes removed.

`records/table08_tolerance_confirmation.csv` provides the records for Table 8. An older confirmation file at tolerance 0.06 does not correspond to that table. An earlier Amazon file that uses a different set of routes does not correspond to Table 15.

## S1.5. Repository

The source code, the controlled-instance generator, the evaluator, and the result records named in S1.4 are in the GitHub repository epsilon-protected-open-routing, release manuscript-20261022, at https://github.com/swx441/epsilon-protected-open-routing/releases/tag/manuscript-20261022. The Amazon Last Mile raw data, the normalized matrices derived from it, and the third-party solvers are not included. The interpreter that holds the pinned packages of S1.1, and that produced the Amazon rerun, is not identified by the run logs, which do not record its minor version. The road-network preprocessing used Python 3.8.0. The host configuration is in S1.2; the physical CPU model is not in the console record and cannot be stated.

## S1.6. Median and Interquartile Range in the 24-Node Main Experiment

The instances, seeds, and budget below are the same as in Tables 9 and 10 and Supplementary Table S4 of the manuscript, and the source of the records is given in S1.4.

### Supplementary Table S2. Median and interquartile range in the 24-node main experiment

| Method | $J_{\mathrm{WL}}$ (IQR) | Tardiness (generated time units, IQR) | Distance (IQR) | Travel time (IQR) |
| --- | --- | --- | --- | --- |
| JWL-ALNS priority-protected | 0.0740 (0.0505) | 350.9 (140.8) | 2861 (321) | 3248 (363) |
| Priority-Greedy | 0.0793 (0.0563) | 556.4 (158.7) | 3760 (641) | 4275 (791) |
| Weighted MLP-ILS | 0.0826 (0.0586) | 447.1 (182.4) | 3703 (689) | 4178 (695) |
| RLS | 0.0824 (0.0588) | 451.1 (196.4) | 3636 (1027) | 4117 (1171) |
| OR-Tools Open-SVRPTW | 0.0824 (0.0122) | 274.5 (182.9) | 2548 (312) | 2870 (347) |
| Native ALNS | 0.0875 (0.0122) | 294.4 (178.8) | 2488 (136) | 2815 (178) |
| Nearest-Neighbor | 0.0877 (0.0136) | 312.6 (163.8) | 3011 (226) | 3382 (263) |

Each cell is the median, with the interquartile range in parentheses. The instances are the same 60 as in Table 9 of the manuscript.

The median of $J_{\mathrm{WL}}$ differs from the mean in Table 9. The gap comes from the concentrated-urgency scenario, whose $J_{\mathrm{WL}}$ clusters near 0.027 and pulls the mean down. With 20 instances in each scenario, the median falls in one of the other two. By the median, JWL-ALNS remained the lowest of the seven methods. The median gaps against the three same-objective heuristics were 0.0053 to 0.0087, larger than the mean gaps of 0.0045 to 0.0051; once the concentrated scenario pulls the mean down, the gap is proportionally larger. The median $J_{\mathrm{WL}}$ of OR-Tools was 0.0824, equal to the values reported for Weighted MLP-ILS and RLS when rounded to three decimal places. Its mean is higher because the concentrated scenario does not pull it down.

The distributions of tardiness and distance point the same way as the means. Against the three same-objective heuristics, the median tardiness of JWL-ALNS was lower by 96 to 205 generated time units and the median distance by 775 to 900. Against OR-Tools and native ALNS the direction reverses: their median tardiness was lower by 56.6 and 77.4 generated time units, respectively, and their median distance was lower by 313 and 374 distance units, respectively. The median distance of Nearest-Neighbor was 150 higher than that of JWL-ALNS, in the same direction as the higher mean distance in Table 9.

### Supplementary Table S3. Median and interquartile range under three service-pressure scenarios

| Scenario | Method | $J_{\mathrm{WL}}$ (IQR) | Tardiness (generated time units, IQR) | Distance (IQR) | Travel time (IQR) |
| --- | --- | --- | --- | --- | --- |
| Concentrated urgency | JWL-ALNS | 0.0271 (0.0030) | 347.2 (105.8) | 2960 (370) | 3364 (444) |
| Concentrated urgency | Priority-Greedy | 0.0285 (0.0034) | 562.7 (38.5) | 4072 (136) | 4628 (182) |
| Concentrated urgency | Weighted MLP-ILS | 0.0271 (0.0025) | 425.4 (94.7) | 3719 (422) | 4195 (489) |
| Concentrated urgency | RLS | 0.0271 (0.0025) | 466.1 (89.8) | 3936 (574) | 4499 (721) |
| Concentrated urgency | OR-Tools | 0.0694 (0.0273) | 210.0 (15.2) | 2532 (216) | 2862 (214) |
| Concentrated urgency | Native ALNS | 0.0777 (0.0287) | 222.2 (23.0) | 2490 (139) | 2817 (185) |
| Routine | JWL-ALNS | 0.0808 (0.0085) | 296.3 (22.4) | 2815 (327) | 3208 (300) |
| Routine | Priority-Greedy | 0.0857 (0.0101) | 378.7 (64.5) | 3392 (245) | 3805 (254) |
| Routine | Weighted MLP-ILS | 0.0869 (0.0068) | 378.8 (60.9) | 3534 (733) | 3975 (777) |
| Routine | RLS | 0.0872 (0.0071) | 320.7 (44.3) | 3060 (444) | 3467 (536) |
| Routine | OR-Tools | 0.0870 (0.0084) | 274.5 (19.4) | 2544 (255) | 2864 (306) |
| Routine | Native ALNS | 0.0879 (0.0064) | 294.4 (28.2) | 2488 (132) | 2815 (141) |
| Tight time windows | JWL-ALNS | 0.0756 (0.0045) | 448.4 (31.1) | 2858 (276) | 3249 (295) |
| Tight time windows | Priority-Greedy | 0.0839 (0.0075) | 592.8 (138.5) | 3685 (498) | 4165 (583) |
| Tight time windows | Weighted MLP-ILS | 0.0854 (0.0054) | 600.9 (81.4) | 3848 (758) | 4356 (770) |
| Tight time windows | RLS | 0.0843 (0.0077) | 604.3 (219.8) | 3761 (1048) | 4352 (1230) |
| Tight time windows | OR-Tools | 0.0834 (0.0087) | 408.9 (14.4) | 2554 (294) | 2875 (324) |
| Tight time windows | Native ALNS | 0.0888 (0.0069) | 424.9 (23.7) | 2488 (132) | 2815 (141) |

Each cell is the median, with the interquartile range in parentheses, over 20 instances per scenario. The median of Nearest-Neighbor over the pooled 60 instances is in Supplementary Table S2.

The medians point the same way as the means in Table 10 and Supplementary Table S4 of the manuscript. In the routine and tight time-window scenarios, the median $J_{\mathrm{WL}}$ of JWL-ALNS was the lowest. Under concentrated urgency it equalled the median of Weighted MLP-ILS and RLS and was lower than that of Priority-Greedy.

The medians of tardiness and distance point the same way. OR-Tools and native ALNS had lower median tardiness and distance in all three scenarios, with the smallest gap in the routine scenario and the largest under concentrated urgency.

## S1.6. Tables Moved Out of the Manuscript

The tables below keep the values that the manuscript no longer prints in full. Each one is cited at its first mention in the manuscript, and none of the values was recomputed.

### Supplementary Table S4. Full stratification of the main experiment under three service-pressure scenarios

The manuscript prints only the four methods that use $J_{\mathrm{WL}}$ as the first key, and only tardiness, distance, and envelope feasibility. This table adds OR-Tools and native ALNS, travel time, and mean $V$. Each block uses 20 instances. The operational envelope is determined by $P^{\mathrm{NN}}$ frozen before the runs, and every method uses the same boundary.

| Scenario | Method | $J_{\mathrm{WL}}$ | Tardiness | Distance | Travel time | Envelope feasible | Mean $V$ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Concentrated urgency | JWL-ALNS | 0.027251 | 323.039 | 2980.850 | 3380.741 | 4/20 | 0.238 |
| Concentrated urgency | Priority-Greedy | 0.028520 | 561.182 | 4097.850 | 4661.215 | 0/20 | 1.528 |
| Concentrated urgency | Weighted MLP-ILS | 0.027210 | 422.782 | 3755.000 | 4259.168 | 0/20 | 0.817 |
| Concentrated urgency | RLS | 0.027210 | 476.405 | 3998.300 | 4544.338 | 0/20 | 1.178 |
| Concentrated urgency | OR-Tools | 0.073972 | 210.993 | 2573.550 | 2913.955 | 20/20 | 0 |
| Concentrated urgency | Native ALNS | 0.076433 | 223.616 | 2509.200 | 2840.828 | 20/20 | 0 |
| Routine | JWL-ALNS | 0.080503 | 299.334 | 2776.100 | 3147.648 | 20/20 | 0 |
| Routine | Priority-Greedy | 0.084962 | 396.139 | 3366.800 | 3815.003 | 2/20 | 0.259 |
| Routine | Weighted MLP-ILS | 0.086292 | 388.829 | 3428.850 | 3881.628 | 3/20 | 0.270 |
| Routine | RLS | 0.087221 | 336.850 | 3168.950 | 3588.436 | 14/20 | 0.116 |
| Routine | OR-Tools | 0.085067 | 275.981 | 2562.850 | 2906.658 | 20/20 | 0 |
| Routine | Native ALNS | 0.088148 | 292.306 | 2501.100 | 2832.025 | 20/20 | 0 |
| Tight time windows | JWL-ALNS | 0.076264 | 447.715 | 2819.050 | 3206.024 | 17/20 | 0.003 |
| Tight time windows | Priority-Greedy | 0.084119 | 626.083 | 3709.050 | 4210.723 | 0/20 | 0.503 |
| Tight time windows | Weighted MLP-ILS | 0.084970 | 601.726 | 3802.600 | 4302.614 | 2/20 | 0.491 |
| Tight time windows | RLS | 0.084783 | 582.284 | 3656.850 | 4146.217 | 7/20 | 0.447 |
| Tight time windows | OR-Tools | 0.085250 | 411.731 | 2613.200 | 2965.028 | 20/20 | 0 |
| Tight time windows | Native ALNS | 0.089522 | 430.802 | 2501.100 | 2832.025 | 20/20 | 0 |

Tardiness and travel time are in generated time units. OR-Tools and native ALNS were envelope-feasible on 20 of 20 instances in every scenario, and under concentrated urgency their $J_{\mathrm{WL}}$ was about 2.7 times that of the same-objective methods.

### Supplementary Table S5. Paired comparisons of tardiness and distance in the 24-node main experiment

$\Delta$ is JWL-ALNS minus the baseline, over the same 60 paired instances as the paired comparison of $J_{\mathrm{WL}}$ in the manuscript, and a negative value means that JWL-ALNS is lower. Intervals come from 2,000 paired bootstrap resamples with seed 20260908. The Holm adjustment covers all 12 rows of this table and is not pooled with the $J_{\mathrm{WL}}$ family. The effect size is the rank-biserial correlation. The paired direction of travel time matches that of distance.

| Baseline | Metric | Mean $\Delta$ | 95% bootstrap interval | Holm-adjusted $p$ | Effect size | Win/tie/loss (%) |
| --- | --- | ---: | --- | ---: | ---: | --- |
| Priority-Greedy | Tardiness | $-171.106$ | [$-192.669$, $-149.667$] | $<0.001$ | $+1.000$ | 100/0/0 |
| Priority-Greedy | Distance | $-865.9$ | [$-964.9$, $-763.0$] | $<0.001$ | $+0.999$ | 96.7/1.7/1.7 |
| Weighted MLP-ILS | Tardiness | $-114.417$ | [$-130.070$, $-100.127$] | $<0.001$ | $+1.000$ | 100/0/0 |
| Weighted MLP-ILS | Distance | $-803.5$ | [$-919.6$, $-686.2$] | $<0.001$ | $+0.972$ | 91.7/0/8.3 |
| RLS | Tardiness | $-108.484$ | [$-134.365$, $-84.874$] | $<0.001$ | $+0.920$ | 85.0/0/15.0 |
| RLS | Distance | $-749.4$ | [$-903.4$, $-596.8$] | $<0.001$ | $+0.948$ | 91.7/0/8.3 |
| OR-Tools | Tardiness | $+57.128$ | [$+43.712$, $+70.662$] | $<0.001$ | $-0.993$ | 3.3/0/96.7 |
| OR-Tools | Distance | $+275.5$ | [$+210.3$, $+340.2$] | $<0.001$ | $-0.869$ | 16.7/0/83.3 |
| Native ALNS | Tardiness | $+41.121$ | [$+27.081$, $+55.189$] | $<0.001$ | $-0.783$ | 18.3/0/81.7 |
| Native ALNS | Distance | $+354.9$ | [$+295.6$, $+415.3$] | $<0.001$ | $-0.978$ | 5.0/0/95.0 |
| Nearest-Neighbor | Tardiness | $+25.793$ | [$+11.769$, $+40.316$] | $0.023$ | $-0.337$ | 38.3/0/61.7 |
| Nearest-Neighbor | Distance | $-124.3$ | [$-195.1$, $-54.0$] | $0.006$ | $+0.438$ | 63.3/0/36.7 |

Against the three heuristics that use $J_{\mathrm{WL}}$ as the first key, both tardiness and distance were lower, every interval lay below zero, and every adjusted $p$-value was below 0.001. Against OR-Tools and native ALNS the direction reverses on both metrics: their routes are shorter and less tardy, and their $J_{\mathrm{WL}}$ is higher. Nearest-Neighbor falls between the two groups, with higher tardiness and lower distance.

### Supplementary Table S6. Results under different urgency curvatures

Changing $\gamma$ changes the urgency weights and therefore the objective itself, so a lower $J_{\mathrm{WL}}$ under a different curvature is not a performance gain. The formal experiment fixes $\gamma=1.0$ before the main experiment, and this table is not used to choose among the curvatures.

| $\gamma$ | $J_{\mathrm{WL}}$ | Tardiness | Distance | Travel time | Instances rewritten |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | 0.0650 | 351.407 | 2853.1 | 3249.066 | 9/15 |
| 1.0 | 0.0620 | 356.491 | 2817.9 | 3216.299 | 5/15 |
| 1.5 | 0.0603 | 355.271 | 2748.1 | 3141.872 | 3/15 |
| 2.0 | 0.0602 | 356.785 | 2768.7 | 3165.652 | 3/15 |

Each row uses five seeds in each of three scenario classes, 15 instances in total, reselected at $\varepsilon=0.05$ from the archive saved at 0.06. Tardiness and travel time are in generated time units. The absolute values are not those of the 24-node main experiment.

### Supplementary Table S7. Protected band under different urgency weights, with absolute values

The manuscript keeps the change columns. This table adds the absolute level of each metric under the same four weight settings.

| $\lambda$ | $J_{\mathrm{WL}}$ | Tardiness | Distance | Travel time | Instances rewritten | $\Delta J_{\mathrm{WL}}$ | Change in tardiness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| $(0,1,0)$ | 0.0460 | 456.834 | 3272.3 | 3711.538 | 23/60 | +0.62% | −1.51% |
| $(0.20,0.20,0.60)$ | 0.0608 | 364.095 | 2939.1 | 3335.985 | 20/60 | +0.53% | −1.49% |
| $(0.20,0.60,0.20)$ | 0.0614 | 356.717 | 2865.2 | 3251.902 | 23/60 | +1.08% | −2.97% |
| $(1/3,1/3,1/3)$ | 0.0617 | 358.548 | 2861.7 | 3247.212 | 20/60 | +0.45% | −1.24% |

$\Delta J_{\mathrm{WL}}$ and the change in tardiness are the protected output relative to the priority-only route of the same search, computed as the ratio of the means. The rows are reselected at $\varepsilon=0.05$ from the archive saved at 0.06. Tardiness and travel time are in generated time units. The absolute values are not those of the 24-node main experiment: 0.0614 is not the 0.061340 reported there.
