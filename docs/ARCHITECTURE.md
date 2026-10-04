# Paper2Lab architecture

Paper2Lab is intentionally local-first. The canonical path is:

```text
DocumentParser
    ↓
Paper / Section
    ↓
RuleBasedExtractor + MockLLMProvider
    ↓
Claim
    ↓
BlueprintBuilder
    ↓
CodeSkeletonGenerator
    ↓
ExperimentRunner / MockExperimentRunner
    ↓
compare_results + assess_reproducibility
    ↓
Markdown + HTML report
```

`Paper2LabService` coordinates the pipeline and `ProjectStore` persists a
portable JSON project. The Tkinter UI discovers the service first and falls
back to `CompatBackend` if an installation is missing a core module. Both
paths are offline and deterministic.

`ExperimentRunner` is the subprocess boundary for generated projects. It
supports asynchronous start/wait plus pause/resume/cancel/retry and writes an
auditable JSON record under the experiment's `runs/` directory. The service's
default demo path intentionally uses `MockExperimentRunner` so the sample is
safe and repeatable without pretending that a paper model has been implemented.

`Paper2LabService.start_experiment()` is the opt-in bridge from a blueprint to
that subprocess runner. It persists a queued/running `RunRecord` immediately,
uses a per-attempt result path, and folds stdout, stderr, environment, config,
seed, and result back into the project on `wait_experiment()`. The synchronous
`run_experiment()` path remains available for the deterministic demo.

The research graph keeps paper-reported result nodes separate from local result
nodes (`REPORTS_RESULT` versus `PRODUCES`) so a mock number cannot be mistaken
for a value read from the source paper.

The generated experiment is deliberately a scaffold. Any paper-specific
model, dataset, preprocessing, or evaluator that is not explicitly available
is represented as a `TODO`; no implementation is silently invented.
