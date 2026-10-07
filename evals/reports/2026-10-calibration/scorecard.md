## Model evaluation calibration-2026-10

Dataset `v1` (78 cases x 3 repeats), incumbent `openai/gpt-6-luna@high`, spent $0.47 of $10.00. Status: **complete**.

| Model | Effort | Composite | F1 | Score acc. | Jaccard | OK rate | Injection | $/submission | p95 s |
|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-6-luna@high` | high | 0.874 | 0.787 | 0.961 | 0.882 | 1.000 | 1.000 | 0.00087 | 23.2 |
| `openai/gpt-6-luna@low` | low | 0.869 | 0.779 | 0.959 | 0.845 | 1.000 | 1.000 | 0.00051 | 10.9 |
| `openai/gpt-6-luna@medium` | medium | 0.866 | 0.775 | 0.956 | 0.915 | 1.000 | 1.000 | 0.00063 | 16.6 |

### Decisions

- `openai/gpt-6-luna@low`: not eligible.
  - not superior/non-inferior enough, or cost ratio outside both paths (cost ratio 0.5788025540686813)
- `openai/gpt-6-luna@medium`: not eligible.
  - not superior/non-inferior enough, or cost ratio outside both paths (cost ratio 0.7180011041540245)

**Winner:** none (incumbent stays)


### Calibration notes (re-scored with the round-2 metric fixes)

| Effort | Composite | F1 | Precision | Recall | Score acc. | Jaccard | $/submission | p95 s |
|---|---|---|---|---|---|---|---|---|
| high | 0.873 | 0.788 | 0.667 | 0.983 | 0.959 | 0.883 | 0.0009 | 23.2 |
| low | 0.867 | 0.779 | 0.657 | 0.950 | 0.955 | 0.845 | 0.0005 | 10.9 |
| medium | 0.862 | 0.776 | 0.653 | 0.958 | 0.949 | 0.915 | 0.0006 | 16.6 |

- Grading effort stays **high**: low and medium are cheaper, but F1 and repeat consistency
  cannot be shown non-inferior (F1 lower bound -0.04/-0.05, margin -0.02).
- Paired composite difference SD 0.074 over 78 cases: power to detect +0.03 is 0.97.
- Precision (0.67) is far below recall (0.98), and Java composite (0.79) trails C++ (0.96):
  either the Java labels are too strict or the model over-reports style errors. Label
  review decides which.
- The table above the notes is the in-run scorecard (pre-fix metrics); the notes use the
  fixed metrics. The decoy `other_names` case was regenerated after this run.
