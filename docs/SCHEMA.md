# Project and experiment schema

The project file uses schema identifier `paper2lab.project.v1` and stores:

- `PaperMetadata`: title, authors, year, venue, abstract, keywords, notes,
  tags, and local path.
- `Section`: normalized title, source content, order and source line span.
- `Claim`: text, evidence, section, importance, claim type, reproducibility
  review state, and required experiment.
- `ExperimentBlueprint`: research question, hypothesis, variables, controls,
  implementation plan, dataset, model, baseline, metrics, paper result, local result, and
  difference records.
- `FormulaNote`: formula/pseudocode/algorithm/parameter text and explanation.
- `RunRecord`: status, stdout/stderr, runtime, config, seed, result and
  environment.

Writes are atomic. A saved project is ordinary UTF-8 JSON so it can be copied,
versioned, or inspected without a database server.
