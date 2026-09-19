"""Agent Registry — structured parse of agent-registry.yaml.

Provides deterministic agent lookup by ID, algorithmic role normalization
(COMPATIBILITY_ONLY boundary), and structured canonical capabilities.
No LLM. Zero silent fallback to software-engineer.

Strictly stdlib + yaml only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Set

from scripts.runtime.routing.errors import RegistryConfigError


# ---------------------------------------------------------------------------
# Role normalization: COMPATIBILITY_ONLY boundary pattern.
# canonical role vocabulary -> algorithmic prefix strip (^\d{2}-) -> legacy exceptions -> registry id.
# Avoids duplicate 44-entry shadow taxonomies.
# ---------------------------------------------------------------------------
LEGACY_ROLE_EXCEPTIONS: Dict[str, str] = {
    "security-specialist": "security-reviewer",
    "10-security-specialist": "security-reviewer",
}


def normalize_role_reference(role_ref: str) -> str:
    """Normalizes role reference: algorithmic prefix strip + minimal legacy exceptions.

    Examples:
        01-requirements-analyst -> requirements-analyst
        06-software-engineer -> software-engineer
        10-security-specialist -> security-reviewer
        software-engineer -> software-engineer
    """
    if not role_ref or not isinstance(role_ref, str):
        return ""
    cleaned = role_ref.strip().lower()
    if cleaned in LEGACY_ROLE_EXCEPTIONS:
        return LEGACY_ROLE_EXCEPTIONS[cleaned]
    # Algorithmic prefix strip: remove leading 2 digits and hyphen
    stripped = re.sub(r"^\d{2}-", "", cleaned)
    if stripped in LEGACY_ROLE_EXCEPTIONS:
        return LEGACY_ROLE_EXCEPTIONS[stripped]
    return stripped


# Compatibility boundary mapping minimal legacy exceptions
ROLE_ALIAS_MAP: Dict[str, str] = dict(LEGACY_ROLE_EXCEPTIONS)


# ---------------------------------------------------------------------------
# Canonical structured capabilities vocabulary (STRUCTURED_CANONICAL)
# ---------------------------------------------------------------------------
CANONICAL_AGENT_CAPABILITIES: Dict[str, frozenset] = {
    "delivery-orchestrator": frozenset({"sdlc-orchestration", "wip-control", "gate-verification", "handoff-enforcement"}),
    "requirements-analyst": frozenset({"invest", "user-stories", "bdd", "gherkin", "nfr", "discovery", "specifications"}),
    "product-owner": frozenset({"product-goal", "value-prioritization", "backlog-ordering", "g1-gate"}),
    "scrum-master": frozenset({"wip-limits", "cycle-time", "kanban", "cfd", "flow"}),
    "solution-architect": frozenset({"c4-model", "adr", "interface-contracts", "threat-modeling", "g2-gate", "systems-architecture"}),
    "data-ai-architect": frozenset({"lakehouse", "data-mesh", "mlflow", "llm-guardrails"}),
    "software-engineer": frozenset({"tdd", "clean-code", "refactoring", "unit-testing", "integration-testing", "solid"}),
    "data-engineer": frozenset({"dags", "dbt", "airflow", "data-quality", "schema-drift", "etl"}),
    "mlops-llmops-engineer": frozenset({"mlflow", "llm-tracing", "prompt-versioning", "drift-detection", "evaluations"}),
    "code-reviewer": frozenset({"static-analysis", "clean-code", "g4-code-gate", "sod", "cognitive-complexity"}),
    "security-reviewer": frozenset({"appsec", "owasp", "stride", "cve-audit", "g4-security-gate", "csaf", "cryptography"}),
    "test-engineer": frozenset({"test-pyramid", "contract-testing", "mutation-testing", "e2e", "test-automation"}),
    "qa-engineer": frozenset({"exploratory-testing", "sbtm", "boundary-testing", "g5-quality-gate"}),
    "devops-release-engineer": frozenset({"gitops", "canary", "blue-green", "ci-cd", "terraform", "iac", "g6-gate"}),
    "governance-auditor": frozenset({"compliance", "segregation-of-duties", "ledger-integrity", "g6-gate", "audit"}),
    "ai-analyst": frozenset({"llm-benchmarking", "hallucination-scoring", "sensitivity-analysis"}),
    "dba-databricks-engineer": frozenset({"delta-lake", "unity-catalog", "dbsql", "vector-search"}),
    "ai-engineer": frozenset({"langgraph", "react-reflexion", "prompt-engineering", "multi-agent"}),
    "skill-curator": frozenset({"skill-vetting", "checksum-verification", "quarantine"}),
    "technical-writer": frozenset({"diataxis", "openapi", "docs-as-code"}),
    "ux-researcher": frozenset({"heuristics", "journey-mapping", "wireframes", "sus-umux"}),
    "frontend-engineer": frozenset({"web-vitals", "react", "semantic-html", "responsive-ui"}),
    "backend-engineer": frozenset({"rest-grpc", "async-io", "caching", "database-pooling"}),
    "data-architect": frozenset({"dimensional-modeling", "3nf", "schema-migration", "data-lineage"}),
    "ml-engineer": frozenset({"feature-engineering", "cross-validation", "model-quantization", "onnx"}),
    "agent-rag-engineer": frozenset({"hybrid-search", "dense-bm25", "reranking", "vector-indexing", "graphrag"}),
    "sre-observability-engineer": frozenset({"sli-slo-sla", "opentelemetry", "prometheus", "grafana", "chaos"}),
    "platform-engineer": frozenset({"idp", "kubernetes-operators", "dev-environments", "developer-self-service"}),
    "performance-engineer": frozenset({"tail-latency", "k6-gatling", "profiling", "flame-graphs"}),
    "integration-engineer": frozenset({"eip", "idempotent-consumers", "webhooks", "message-queues"}),
    "brand-strategist": frozenset({"brand-gap", "zag-differentiation", "brand-matrix"}),
    "direct-response-copywriter": frozenset({"customer-awareness", "headline-formulas", "cta-optimization"}),
    "growth-marketing-strategist": frozenset({"pirate-metrics", "growth-loops", "plg", "retention"}),
    "storytelling-strategist": frozenset({"storybrand", "sparkline", "pitch-decks"}),
    "offensive-cyber-operator": frozenset({"mitre-attack", "penetration-testing", "exploit-validation", "red-team"}),
    "swarm-consensus-coordinator": frozenset({"bft", "crdt", "quorum", "dkg"}),
    "fullstack-engineer": frozenset({"nextjs", "trpc-zod", "prisma-drizzle", "fullstack"}),
    "mobile-engineer": frozenset({"flutter", "react-native", "swift-swiftui", "kotlin-compose", "mobile"}),
    "cloud-architect": frozenset({"well-architected", "terraform", "multi-cloud", "zero-trust", "finops"}),
    "agile-coach": frozenset({"fibonacci-sizing", "max-8-sp", "littles-law", "cfd", "dora-metrics"}),
    "ui-designer": frozenset({"atomic-design", "design-tokens", "wcag-aaa", "css-architecture"}),
}


@dataclass(frozen=True)
class AgentCapability:
    """Structured representation of a single agent from the registry."""

    agent_id: str
    path: str
    title: str
    mode: str  # host | core | on_demand
    purpose: str
    capabilities: frozenset  # STRUCTURED_CANONICAL source
    dispatchable: bool
    singleton: bool


def extract_diagnostic_keywords(purpose: str) -> frozenset:
    """Non-authoritative diagnostic helper.

    MUST NOT decide canonical assignment. Retained solely for backwards
    diagnostic introspection.
    """
    if not purpose:
        return frozenset()

    noise = {
        "and", "or", "the", "a", "an", "for", "of", "in", "to", "with",
        "via", "from", "on", "by", "at", "as", "is", "are", "be", "can",
    }
    raw_parts = purpose.replace("(", ",").replace(")", ",").split(",")
    caps: set = set()
    for part in raw_parts:
        cleaned = part.strip().lower()
        if len(cleaned) > 2 and cleaned not in noise:
            caps.add(cleaned)
    return frozenset(caps)


# Deprecated alias for test compatibility
_extract_capabilities = extract_diagnostic_keywords


class AgentRegistry:
    """Deterministic agent registry parsed from agent-registry.yaml data.

    Provides:
    - Lookup by agent_id (exact match)
    - Lookup by normalized role (COMPATIBILITY_ONLY boundary)
    - Structured canonical capability search
    - Dispatchable agent filtering
    """

    def __init__(self, registry_data: dict) -> None:
        if not registry_data or not isinstance(registry_data, dict):
            raise RegistryConfigError("Registry data must be a non-empty dict")

        agents_raw = registry_data.get("agents", [])
        if not agents_raw:
            raise RegistryConfigError("Registry contains no agents")

        self._agents: Dict[str, AgentCapability] = {}
        for agent_raw in agents_raw:
            if not isinstance(agent_raw, dict):
                continue
            agent_id = agent_raw.get("id", "").strip()
            if not agent_id:
                continue

            # STRUCTURED_CANONICAL capability resolution:
            # 1. Structured 'capabilities' list in registry YAML
            # 2. Canonical structured mapping
            raw_caps = agent_raw.get("capabilities")
            if raw_caps and isinstance(raw_caps, list):
                caps = frozenset(c.strip().lower() for c in raw_caps if isinstance(c, str) and c.strip())
            elif agent_id in CANONICAL_AGENT_CAPABILITIES:
                caps = CANONICAL_AGENT_CAPABILITIES[agent_id]
            else:
                caps = frozenset()

            cap = AgentCapability(
                agent_id=agent_id,
                path=agent_raw.get("path", ""),
                title=agent_raw.get("title", ""),
                mode=agent_raw.get("mode", "on_demand"),
                purpose=agent_raw.get("purpose", ""),
                capabilities=caps,
                dispatchable=agent_raw.get("dispatchable", True),
                singleton=agent_raw.get("singleton", False),
            )
            self._agents[agent_id] = cap

        if not self._agents:
            raise RegistryConfigError("Registry parsed zero valid agents")

    def get_agent(self, agent_id: str) -> Optional[AgentCapability]:
        """Returns agent by exact registry ID, or None if not found."""
        return self._agents.get(agent_id)

    def resolve_role(self, role_ref: str) -> Optional[AgentCapability]:
        """Resolves a role reference (direct or normalized) to an agent.

        Tries:
        1. Direct registry ID match
        2. Algorithmic prefix strip and legacy normalization
        """
        if not role_ref:
            return None

        # Direct match
        agent = self._agents.get(role_ref)
        if agent is not None:
            return agent

        # Normalized lookup via COMPATIBILITY_ONLY boundary
        normalized_id = normalize_role_reference(role_ref)
        if normalized_id and normalized_id in self._agents:
            return self._agents[normalized_id]

        return None

    def find_by_capability(self, capability: str) -> List[AgentCapability]:
        """Returns all agents possessing the structured capability."""
        search = capability.strip().lower()
        results: List[AgentCapability] = []
        for agent in self._agents.values():
            for cap in agent.capabilities:
                if search == cap or search in cap:
                    results.append(agent)
                    break
        return results

    def find_dispatchable(self) -> List[AgentCapability]:
        """Returns all agents that can be dispatched (dispatchable=True)."""
        return [a for a in self._agents.values() if a.dispatchable]

    def list_all(self) -> List[AgentCapability]:
        """Returns all registered agents."""
        return list(self._agents.values())
