| item | verdict | reason |
|---|---|---|
| 1 | YES | Names features.yaml ConfigMap and its YAML syntax error |
| 2 | PARTIAL | HPA cannot fetch custom metrics; no APIService location |
| 3 | PARTIAL | Connection refused to inventory; no port mismatch |
| 4 | NO | Blames pricing replica drift |
| 5 | NO | Names a fault; no incident |
| 6 | NO | Names drift faults; no incident |
| 7 | PARTIAL | Config file fails to load; no ConfigMap |
| 8 | NO | Names CPU fault; no incident |
| 9 | YES | Names features.yaml ConfigMap and its YAML parse error |
| 10 | NO | Blames replicas count, not HPA policy |
| 11 | NO | Names minReadySeconds fault; no incident |
| 12 | NO | Names image drift; no incident |
| 13 | NO | Names paused deployments; no incident |
| 14 | NO | Names paused deployments; no incident |
| 15 | NO | Names image change; no incident |
| 16 | NO | Quota only; no CronJob or reports-db |
| 17 | PARTIAL | Image cannot be pulled; injector not named |
| 18 | NO | Names paused alertmanager; no incident |
| 19 | NO | Blames paused deployment |
| 20 | NO | DB connection issue; no label or policy |
| 21 | NO | Names rollout settings fault; no incident |
| 22 | NO | Insufficient evidence |
| 23 | PARTIAL | Memory limit above max; LimitRange not named |
| 24 | NO | Blames pricing replica drift |
| 25 | NO | Vague namespace resources; no memory limit or LimitRange |
| 26 | PARTIAL | Pricing failing; Redis limit not named |
| 27 | NO | Insufficient evidence |
| 28 | NO | Names container fault; no incident |
| 29 | NO | Names paused deployment; no incident |
| 30 | NO | Names image drift; no incident |
| 31 | NO | Generic network issue; no NetworkPolicy or label |
| 32 | PARTIAL | Image pull failure; injector not named |
| 33 | PARTIAL | DB host unresolvable; ConfigMap not named |
| 34 | NO | Blames paused deployment |
| 35 | NO | Blames rollingUpdate strategy |
| 36 | NO | DB connection issue; no label or policy |
| 37 | NO | Only i/o timeout; no policy or label |
| 38 | PARTIAL | Limit exceeds per-container max; LimitRange not named |
| 39 | NO | Names paused deployment; no incident |
| 40 | YES | Says no incident |
| 41 | NO | Names replica fault; no incident |
| 42 | NO | Blames payments service failure |
| 43 | PARTIAL | Image not found; injector not named |
| 44 | PARTIAL | DB host unresolvable; ConfigMap not named |
| 45 | NO | Quota and OOM; no CronJob |
| 46 | NO | Names replica fault; no incident |
| 47 | NO | Names paused deployments; no incident |
| 48 | NO | Blames payments service failure |
| 49 | NO | Insufficient evidence |
| 50 | NO | DB i/o timeout; no label or policy |
| 51 | NO | Wrong HPA/PDB mechanism; no scale-up policy |
| 52 | NO | Names paused deployments; no incident |
| 53 | PARTIAL | Memory limits too low; LimitRange not named |
| 54 | NO | Names minReadySeconds fault; no incident |
| 55 | PARTIAL | Inventory unreachable; no port mismatch |
| 56 | NO | Billing-worker quota only; no catalog-api |
| 57 | NO | Names imagePullPolicy fault; no incident |
| 58 | PARTIAL | DB host unresolvable; ConfigMap not named |
| 59 | NO | Names OOMKill fault; no incident |
| 60 | YES | Names features.yaml ConfigMap and its YAML syntax error |
| 61 | NO | Quota only; no catalog-api |
| 62 | NO | Names paused deployment; no incident |
| 63 | PARTIAL | HPA cannot fetch custom metrics; no APIService location |
| 64 | NO | Blames paused billing-worker |
| 65 | NO | Insufficient evidence |
| 66 | NO | Quota only; no CronJob |
| 67 | NO | Names paused deployment; no incident |
| 68 | NO | OOM and quota; no catalog-api |
| 69 | NO | Names paused deployments; no incident |
| 70 | NO | Names paused alertmanager; no incident |
| 71 | NO | Names container fault; no incident |
| 72 | NO | Names paused deployment; no incident |
| 73 | PARTIAL | Inventory connection issues; no port |
| 74 | PARTIAL | Pricing cache write failure; Redis limit not named |
| 75 | NO | Billing-worker quota only; no catalog-api |
| 76 | PARTIAL | Bad dbHost found but blamed on Helm values, not shop-env |
| 77 | NO | Blames payments network; no policy |
| 78 | NO | Insufficient evidence |
| 79 | NO | Quota and drift; no CronJob |
| 80 | NO | Names paused alertmanager; no incident |
| 81 | NO | Names paused deployment; no incident |
| 82 | NO | Insufficient evidence |
| 83 | NO | Confused limits blamed on alertmanager |
| 84 | NO | Names image change; no incident |
| 85 | NO | Names replica fault; no incident |
| 86 | NO | Blames alertmanager |
| 87 | PARTIAL | Image cannot be pulled; injector not named |
| 88 | PARTIAL | Config file misconfigured; ConfigMap not named |
| 89 | PARTIAL | DB host unresolvable; ConfigMap not named |
| 90 | YES | Says no incident |
| 91 | PARTIAL | Redis OOM mentioned but misplaced in pricing; policy unclear |
| 92 | NO | Names paused deployments; no incident |
| 93 | NO | Blames pricing replica drift |
| 94 | YES | Names expired mesh-injector webhook certificate |
| 95 | NO | Says no incident when there is one |
| 96 | PARTIAL | OOMKilled over memory limit; LimitRange not named |
| 97 | NO | DB connection issue; no label or policy |
| 98 | YES | Names expired mesh-injector webhook certificate |
| 99 | NO | Names CPU fault; no incident |
| 100 | NO | Billing-worker quota only; no catalog-api |
| 101 | YES | Says no incident |
| 102 | NO | DB connection issue; no label or policy |
| 103 | NO | Blames paused deployment |
| 104 | NO | Names paused deployment; no incident |
| 105 | YES | Names expired mesh-injector-tls certificate breaking the webhook |
| 106 | PARTIAL | Connection refused to inventory; no port |
| 107 | YES | Says no incident |
| 108 | NO | Blames minReadySeconds |
| 109 | NO | DB connection issue; no label or policy |
| 110 | PARTIAL | YAML file parse error; ConfigMap not named |
| 111 | PARTIAL | HPA cannot fetch custom metrics; plus wrong minReadySeconds cause |
| 112 | NO | Blames CPU overconsumption; no limit source |
| 113 | PARTIAL | Inventory connection issues; no port |
| 114 | PARTIAL | Image pull failure; injector not named |
| 115 | NO | Says no incident when there is one |
| 116 | NO | Names paused deployment; no incident |
| 117 | YES | Names expired mesh-injector-tls certificate |
| 118 | NO | Only i/o timeout; no policy or label |
| 119 | NO | Names CPU fault; no incident |
| 120 | PARTIAL | Limit above 1Gi max; LimitRange not named |
| 121 | PARTIAL | Metrics APIService unavailable; not where it points |
| 122 | PARTIAL | Inventory unreachable, connection refused; no port |
| 123 | NO | Quota only; no CronJob |
| 124 | NO | Blames restart policies |
| 125 | NO | Wrong mechanism: usage exceeds limit; no LimitRange |
| 126 | NO | Quota only; no CronJob |
| 127 | YES | Names expired mesh-injector-tls certificate |
| 128 | NO | Quota only; no CronJob |
| 129 | NO | Quota plus image drift; no catalog-api |
| 130 | NO | Names image update; no incident |
| 131 | NO | Names minReadySeconds and pause; no incident |
| 132 | PARTIAL | Limit above max rejected; LimitRange not named |
| 133 | NO | Names probe failure; no incident |
| 134 | PARTIAL | Image pull failure; injector not named |
| 135 | NO | Names rollout strategy fault; no incident |
| 136 | YES | Says no incident |
| 137 | NO | Blames insufficient resources |
| 138 | NO | Names paused deployments; no incident |
| 139 | NO | Says no incident when there is one |
| 140 | NO | Blames pricing replica drift |
| 141 | YES | Says no incident |
| 142 | PARTIAL | Image pull failure; injector not named |
| 143 | NO | Generic DB connectivity; no host or ConfigMap |
| 144 | NO | Names maxSurge fault; no incident |
| 145 | NO | Names paused deployment; no incident |
| 146 | NO | Blames replicas setting |
| 147 | YES | Names expired mesh-injector webhook certificate |
