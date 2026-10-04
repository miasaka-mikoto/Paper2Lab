"""Public extractor entry point.

The implementation lives in :mod:`paper2lab.claims`; this small module keeps
the feature discoverable for integrations that naturally look for an
``extractor`` module while preserving the explicit offline RuleBased API.
"""

from .claims import ClaimExtractor, RuleBasedClaimExtractor, RuleBasedExtractor, extract_claims

__all__ = ["ClaimExtractor", "RuleBasedClaimExtractor", "RuleBasedExtractor", "extract_claims"]
