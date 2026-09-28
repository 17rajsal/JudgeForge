# JudgeForge Judging & Normalization Engine

Fair hackathon judging requires balancing two conflicting realities:
1. **Judge Heterogeneity**: Different judges have different internal baselines. A "strict" judge might give top projects a 3.5, while a "generous" judge hands out 5.0s freely.
2. **Sparse Incomplete Batches**: Projects are not reviewed by every judge. A project evaluated only by strict judges would unfairly lose to a project evaluated by generous judges if scores were simply averaged.

JudgeForge solves this with **Z-Score standardization with zero-variance defense**.

---

## 1. Rubric & Raw Score Computation

Each evaluation comprises ratings across defined rubric dimensions:
- **Functionality** ($s_{\text{func}} \in [1, 5]$)
- **Code & Design Quality** ($s_{\text{qual}} \in [1, 5]$)
- **Innovation & Creativity** ($s_{\text{innov}} \in [1, 5]$)

Each dimension has an organizer-configurable weight $w_c \ge 0$.

### Raw Score Formula

For a given review $k$ by judge $j$ on project $p$:

$$\text{Raw Score}_{j, p} = \frac{\sum_{c} w_c \cdot s_{c, j, p}}{\sum_{c} w_c}$$

With default equal weights ($w_{\text{func}} = 1.0, w_{\text{qual}} = 1.0, w_{\text{innov}} = 1.0$):

$$\text{Raw Score}_{j, p} = \frac{s_{\text{func}} + s_{\text{qual}} + s_{\text{innov}}}{3.0}$$

---

## 2. Statistical Normalization Formula

### Step A: Judge Baseline Metrics

For each judge $j$ who submitted $N_j$ reviews, let $\{s_{j, 1}, s_{j, 2}, \dots, s_{j, N_j}\}$ be the set of raw scores they awarded.

1. **Judge Mean ($\mu_j$)**:
   $$\mu_j = \frac{1}{N_j} \sum_{k=1}^{N_j} s_{j, k}$$

2. **Judge Variance and Standard Deviation ($\sigma_j$)**:
   $$\sigma_j = \sqrt{ \frac{1}{N_j} \sum_{k=1}^{N_j} (s_{j, k} - \mu_j)^2 }$$

### Step B: Global Population Baseline

Across all $M$ submitted scores in the competition:

$$\mu_{\text{global}} = \frac{1}{M} \sum_{m=1}^{M} s_m, \quad \sigma_{\text{global}} = \sqrt{\frac{1}{M} \sum_{m=1}^{M} (s_m - \mu_{\text{global}})^2}$$

### Step C: Standardized Z-Score Calculation

For each score $s_{j, p}$ given by judge $j$ to project $p$, compute the standardized Z-score:

$$z_{j, p} = \begin{cases} \dfrac{s_{j, p} - \mu_j}{\sigma_j} & \text{if } \sigma_j > 10^{-6} \\ 0.0 & \text{if } \sigma_j \le 10^{-6} \text{ (Zero Variance Case)} \end{cases}$$

### Step D: Rescaling to Rubric Domain

To maintain intuitive interpretability for organizers and participants, Z-scores are mapped back to the 1.0–5.0 rubric scale:

$$s^{\text{norm}}_{j, p} = \text{clamp}\left( \mu_{\text{global}} + z_{j, p} \cdot \sigma_{\text{global}}, \, 1.0, \, 5.0 \right)$$

### Step E: Project Aggregate Score

For a project $p$ that received $K_p$ reviews:

$$\text{Final Normalized Score}_p = \frac{1}{K_p} \sum_{j \in \text{Judges}(p)} s^{\text{norm}}_{j, p}$$

---

## 3. Handling Edge Cases

### Case 1: Zero-Variance Reviewer ($\sigma_j = 0$)
- **Symptom**: A judge awards identical ratings to every project they review (e.g. giving 4 to everything).
- **The Risk**: Division by zero ($\frac{s - \mu}{0}$) produces `NaN` or crashes the calculation pipeline.
- **JudgeForge Solution**: When $\sigma_j \le 10^{-6}$, the system assigns $z_{j, p} = 0.0$. This treats their assessment as neutral (exactly matching the global mean $\mu_{\text{global}}$) without distorting relative project rankings.

### Case 2: Unequal & Incomplete Review Batches ($K_p$ varies)
- **Symptom**: One project has 2 reviews, while another has 5 reviews.
- **The Risk**: Raw averages reward projects lucky enough to be graded by lenient judges.
- **JudgeForge Solution**: Because every score is standardized against the individual reviewer's personal distribution before averaging, a 4 from a tough judge translates to a high positive Z-score, while a 4 from an easy judge translates to a neutral or negative Z-score.

### Case 3: Missing Scores
- Projects without reviews receive a score of `0.0` and are placed at the bottom of the leaderboard.

### Case 4: Tie Breaking
- Ties in normalized score are resolved deterministically:
  1. Higher **Normalized Score** ($s^{\text{norm}}$)
  2. Higher **Raw Score Average** ($\bar{s}^{\text{raw}}$)
  3. Lower Project ID (chronological submission priority)

### Case 5: Rounding and Precision
- Internal calculations preserve full 64-bit IEEE 754 floating-point precision.
- CSV export formats scores to 4 decimal places (`{val:.4f}`) to ensure transparency.

---

## 4. Privacy & Authorization Architecture

- **Judge Isolation**: A judge cannot see scores submitted by other judges. Server-side validation in `app/routers/judging.py` verifies that `current_user.judge_id == target_judge_id` on all score query routes.
- **Participant Redaction**: Participants cannot read judging scores or progress metrics. Calling `/api/judge/scores` or `/api/export.csv` as a participant results in `HTTP 403 Forbidden`.
- **Audit Logging**: Every score creation or update triggers an immutable record in `audit_logs` storing user ID, project ID, criteria breakdown, and timestamp.
