| item | verdict | reason in at most 15 words |
|---:|:---:|---|
| 1 | YES | Identifies malformed YAML in the ConfigMap used by both applications. |
| 2 | PARTIAL | Identifies unavailable custom metrics, but not the APIService targeting the old adapter namespace. |
| 3 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 4 | NO | Unrelated replica drift; neither Redis memory exhaustion nor pricing failure is explained. |
| 5 | NO | Invents a fault despite no incident in ground truth. |
| 6 | NO | Invents a fault despite no incident in ground truth. |
| 7 | PARTIAL | Names malfunctioning features.yaml but omits its ConfigMap and explicit parse failure. |
| 8 | NO | Invents a fault despite no incident in ground truth. |
| 9 | YES | Identifies malformed YAML in the ConfigMap used by both applications. |
| 10 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 11 | NO | Invents a fault despite no incident in ground truth. |
| 12 | NO | Invents a fault despite no incident in ground truth. |
| 13 | NO | Invents a fault despite no incident in ground truth. |
| 14 | NO | Invents a fault despite no incident in ground truth. |
| 15 | NO | Invents a fault despite no incident in ground truth. |
| 16 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 17 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 18 | NO | Invents a fault despite no incident in ground truth. |
| 19 | NO | Misses the APIService pointing to the relocated metrics adapter. |
| 20 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 21 | NO | Invents a fault despite no incident in ground truth. |
| 22 | NO | Provides no causal diagnosis of the inventory Service port mismatch. |
| 23 | PARTIAL | Suggests resource-limit rejection, but reverses threshold values and omits LimitRange. |
| 24 | PARTIAL | Blames pricing replica drift rather than Redis refusing writes at maxmemory. |
| 25 | NO | Mentions OOMKills but not an insufficient memory limit or its LimitRange source. |
| 26 | PARTIAL | Recognizes pricing errors but provides only latency and error-rate symptoms. |
| 27 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 28 | NO | Invents a fault despite no incident in ground truth. |
| 29 | NO | Invents a fault despite no incident in ground truth. |
| 30 | NO | Invents a fault despite no incident in ground truth. |
| 31 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 32 | PARTIAL | Finds the missing image but incorrectly adds ephemeral-storage exhaustion; injector unmentioned. |
| 33 | PARTIAL | Identifies unresolvable database hostname, without the shop-env ConfigMap DB_HOST change. |
| 34 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 35 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 36 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 37 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 38 | PARTIAL | Mentions exceeded per-container maximum; omits the responsible LimitRange. |
| 39 | NO | Invents a fault despite no incident in ground truth. |
| 40 | YES | Correctly reports no incident and names no fault. |
| 41 | NO | Invents a fault despite no incident in ground truth. |
| 42 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 43 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 44 | PARTIAL | Identifies unresolvable database hostname, without the shop-env ConfigMap DB_HOST change. |
| 45 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 46 | NO | Invents a fault despite no incident in ground truth. |
| 47 | NO | Invents a fault despite no incident in ground truth. |
| 48 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 49 | NO | Does not identify the shop-env ConfigMap DB_HOST change or unresolvable hostname. |
| 50 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 51 | PARTIAL | Names HPA configuration but blames unrelated scale-down and disruption settings. |
| 52 | NO | Invents a fault despite no incident in ground truth. |
| 53 | PARTIAL | Mentions possibly insufficient memory limits, but not the LimitRange injecting 256Mi. |
| 54 | NO | Invents a fault despite no incident in ground truth. |
| 55 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 56 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 57 | NO | Invents a fault despite no incident in ground truth. |
| 58 | PARTIAL | Identifies unresolvable database hostname, without the shop-env ConfigMap DB_HOST change. |
| 59 | NO | Invents a fault despite no incident in ground truth. |
| 60 | YES | Identifies malformed YAML in the ConfigMap used by both applications. |
| 61 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 62 | NO | Invents a fault despite no incident in ground truth. |
| 63 | PARTIAL | Identifies unavailable custom metrics, but not the APIService targeting the old adapter namespace. |
| 64 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 65 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 66 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 67 | NO | Invents a fault despite no incident in ground truth. |
| 68 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 69 | NO | Invents a fault despite no incident in ground truth. |
| 70 | NO | Invents a fault despite no incident in ground truth. |
| 71 | NO | Invents a fault despite no incident in ground truth. |
| 72 | NO | Invents a fault despite no incident in ground truth. |
| 73 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 74 | PARTIAL | Describes pricing cache-write failures without Redis memory exhaustion or eviction policy. |
| 75 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 76 | PARTIAL | Finds the invalid dbHost value in Helm values, not its shop-env ConfigMap. |
| 77 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 78 | NO | Does not identify the malformed ConfigMap or the parsing failure. |
| 79 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 80 | NO | Invents a fault despite no incident in ground truth. |
| 81 | NO | Invents a fault despite no incident in ground truth. |
| 82 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 83 | PARTIAL | Notes a memory maximum problem but attributes thresholds to unrelated Deployments. |
| 84 | NO | Invents a fault despite no incident in ground truth. |
| 85 | NO | Invents a fault despite no incident in ground truth. |
| 86 | NO | Misses the LimitRange-imposed 256Mi default causing OOMKills. |
| 87 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 88 | PARTIAL | Identifies faulty features.yaml, without the ConfigMap or explicit parse error. |
| 89 | PARTIAL | Identifies unresolvable database hostname, without the shop-env ConfigMap DB_HOST change. |
| 90 | YES | Correctly reports no incident and names no fault. |
| 91 | PARTIAL | Links Redis OOM to pricing errors but not maxmemory write rejection or eviction policy. |
| 92 | NO | Invents a fault despite no incident in ground truth. |
| 93 | PARTIAL | Points to pricing failure but misses Redis maxmemory/noeviction refusing writes. |
| 94 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
| 95 | NO | Misses the LimitRange admission rejection of the search-api pod. |
| 96 | PARTIAL | Reports OOMKills at the memory limit, but not its LimitRange source. |
| 97 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 98 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
| 99 | NO | Invents a fault despite no incident in ground truth. |
| 100 | NO | Identifies quota pressure but misses catalog-api CPU-request drift and HPA amplification. |
| 101 | YES | Correctly reports no incident and names no fault. |
| 102 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 103 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 104 | NO | Invents a fault despite no incident in ground truth. |
| 105 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
| 106 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 107 | YES | Correctly reports no incident and names no fault. |
| 108 | NO | Misses the APIService pointing to the relocated metrics adapter. |
| 109 | NO | Misses the removed pod label and consequent NetworkPolicy selector mismatch. |
| 110 | PARTIAL | Describes YAML parsing failure, but does not name the feature-flags ConfigMap. |
| 111 | PARTIAL | Finds the custom-metrics failure but adds an unrelated minReadySeconds explanation. |
| 112 | NO | Blames CPU throttling and generic memory shortage, not the imposed LimitRange default. |
| 113 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 114 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 115 | NO | Misses the APIService pointing to the relocated metrics adapter. |
| 116 | NO | Invents a fault despite no incident in ground truth. |
| 117 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
| 118 | NO | Misses the namespace-label mismatch with the payments NetworkPolicy. |
| 119 | NO | Invents a fault despite no incident in ground truth. |
| 120 | PARTIAL | Describes admission refusal from a 1Gi maximum versus 2Gi limit, without LimitRange. |
| 121 | PARTIAL | Identifies the unavailable aggregated metrics API, not its outdated adapter target. |
| 122 | PARTIAL | Identifies inventory connectivity failures but misses the Service-to-pod port mismatch. |
| 123 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 124 | NO | Misses the LimitRange-imposed 256Mi default causing OOMKills. |
| 125 | NO | Mistakes admission-time limit validation for runtime memory exceeding a 2Gi limit. |
| 126 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 127 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
| 128 | NO | Identifies quota exhaustion, not the report-export CronJob accumulating blocked Jobs. |
| 129 | NO | Correctly notes quota exhaustion, but attributes causality to unrelated image drift. |
| 130 | NO | Invents a fault despite no incident in ground truth. |
| 131 | NO | Invents a fault despite no incident in ground truth. |
| 132 | PARTIAL | Recognizes 1Gi maximum versus 2Gi limit, but omits LimitRange and misnames scheduler. |
| 133 | NO | Invents a fault despite no incident in ground truth. |
| 134 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 135 | NO | Invents a fault despite no incident in ground truth. |
| 136 | YES | Correctly reports no incident and names no fault. |
| 137 | NO | Does not identify the HPA scale-up rate limit or stabilization window. |
| 138 | NO | Invents a fault despite no incident in ground truth. |
| 139 | NO | Incorrectly reports no incident despite the expired webhook certificate. |
| 140 | PARTIAL | Blames pricing replica drift rather than exhausted Redis memory. |
| 141 | YES | Correctly reports no incident and names no fault. |
| 142 | PARTIAL | Identifies failed image pulls, not the mesh-injector injecting the nonexistent image. |
| 143 | NO | Mentions database connectivity only; neither hostname resolution nor ConfigMap is identified. |
| 144 | NO | Invents a fault despite no incident in ground truth. |
| 145 | NO | Invents a fault despite no incident in ground truth. |
| 146 | NO | Misses the LimitRange-imposed 256Mi default causing OOMKills. |
| 147 | YES | Identifies the expired mesh-injector TLS certificate blocking admission. |
