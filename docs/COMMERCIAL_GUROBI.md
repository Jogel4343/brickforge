# Gurobi commercial licensing

LegoGPT's stability analysis uses [Gurobi](https://www.gurobi.com/), a commercial
mixed-integer programming solver. Two licensing implications:

## v1 (academic / development)

You qualify for a **free Gurobi academic license** as a University of Virginia
student. Register at https://www.gurobi.com/academia/academic-program-and-licenses/
with your `.virginia.edu` email. License is renewable annually. Works for all
non-commercial use including development and academic publishing.

**This unblocks v1 entirely.** Use it through the build phase.

## Commercial launch (Phase 4+)

When you start charging for designs, an academic license is no longer compliant.
Options, in order of preference:

### Option A — Swap to open-source MIP solvers (recommended)

Replace Gurobi with [HiGHS](https://highs.dev/), [OR-Tools](https://developers.google.com/optimization),
or [SCIP](https://www.scipopt.org/) for the stability checker. All are:
- Free for any use including commercial
- Reasonably performant for problems of LegoGPT's size
- ~10–30% slower than Gurobi in practice (acceptable for our use)

**Engineering cost**: ~1–2 weeks to port the stability checker. Schedule for
late Phase 1 (week 8 polish) or early Phase 2.

### Option B — Gurobi WLS / cloud licensing

Gurobi sells [WLS (Web License Service)](https://www.gurobi.com/features/web-license-service/)
for cloud deployments. Pricing is opaque and negotiable. Expect $5K–$25K/year
for small commercial use. Worth a quote conversation when revenue justifies it.

### Option C — Hybrid

Use Gurobi for premium / paid designs (where the cost per design is amortized
across higher AOV), and HiGHS/OR-Tools for the free tier and gift kits. Adds
complexity.

## Decision

**Default plan: Option A.** Swap Gurobi for HiGHS before opening the marketplace
(Phase 4). Track as a v1.5 engineering task.
