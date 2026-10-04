"""Paper2Lab — local-first paper-to-experiment workbench.

The public API is intentionally small and standard-library based.  Optional
PDF readers are used only when already installed; no network or paid model API
is contacted by the development build.
"""

__version__ = "0.1.0"

from .models import (  # noqa: E402,F401
    Claim,
    ClaimType,
    ExperimentBlueprint,
    FormulaNote,
    MetricComparison,
    Paper,
    PaperMetadata,
    RunRecord,
    RunStatus,
    Section,
)
from .parser import DocumentParser, ParsedDocument  # noqa: E402,F401
from .claims import RuleBasedClaimExtractor, RuleBasedExtractor  # noqa: E402,F401
from .providers import (  # noqa: E402,F401
    DeferredProvider,
    FUTURE_PROVIDER_NAMES,
    LLMProvider,
    MockLLMProvider,
    ProviderRegistry,
    ProviderResponse,
    RuleBasedProvider,
    default_registry,
)
from .storage import ProjectStore  # noqa: E402,F401
from .service import Paper2LabService  # noqa: E402,F401
from .blueprint import BlueprintBuilder, CodeSkeletonGenerator  # noqa: E402,F401
from .generator import ExperimentSkeletonGenerator, GeneratedExperiment, generate_experiment_skeleton  # noqa: E402,F401
from .runner import ExperimentRunner, MockExperimentRunner, RunRequest, RunResult, run_mock_experiment  # noqa: E402,F401
from .compare import compare_metric, compare_results, comparison_summary  # noqa: E402,F401
from .reproducibility import assess_reproducibility, score_reproducibility, ReproducibilityAssessment  # noqa: E402,F401
from .report import ReportGenerator, generate_html_report, generate_markdown_report  # noqa: E402,F401

__all__ = [
    "Claim", "ClaimType", "ExperimentBlueprint", "FormulaNote", "MetricComparison",
    "Paper", "PaperMetadata", "RunRecord", "RunStatus", "Section",
    "DocumentParser", "ParsedDocument", "RuleBasedClaimExtractor", "RuleBasedExtractor",
    "LLMProvider", "ProviderResponse", "MockLLMProvider", "RuleBasedProvider", "ProviderRegistry", "default_registry", "DeferredProvider", "FUTURE_PROVIDER_NAMES", "ProjectStore",
    "Paper2LabService", "BlueprintBuilder", "CodeSkeletonGenerator",
    "ExperimentSkeletonGenerator", "GeneratedExperiment", "generate_experiment_skeleton",
    "ExperimentRunner", "MockExperimentRunner", "RunRequest", "RunResult", "run_mock_experiment",
    "compare_metric", "compare_results", "comparison_summary",
    "assess_reproducibility", "score_reproducibility", "ReproducibilityAssessment",
    "ReportGenerator", "generate_html_report", "generate_markdown_report",
]
