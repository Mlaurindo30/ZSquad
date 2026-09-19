"""
R3 Work Item Canonical Identifier Suite (Section 31).

Tests canonical grammar, prefix invariants, legacy alias recognition and normalization,
digit padding constraints, and anti-legacy emission for newly generated items.
"""

from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemId, WorkItemKind
from scripts.runtime.work_items.ids import CanonicalIdService


class TestR3CanonicalWorkItemIds:
    """Suíte canônica de testes para identificadores de Work Items (R3 - Seção 31)."""

    def test_canonical_ids_accepted(self):
        """EPIC-001, FEATURE-001, STORY-001 e TASK-0001 aceitos."""
        assert CanonicalIdService.validate("EPIC-001")
        assert CanonicalIdService.validate("FEATURE-001")
        assert CanonicalIdService.validate("STORY-001")
        assert CanonicalIdService.validate("TASK-0001")

        # Verificação cruzada com domínio WorkItemId
        assert WorkItemId.validate("EPIC-001")
        assert WorkItemId.validate("FEATURE-001")
        assert WorkItemId.validate("STORY-001")
        assert WorkItemId.validate("TASK-0001")

    def test_operational_canonical_ids_accepted(self):
        """Tipos operacionais canônicos aceitos."""
        assert CanonicalIdService.validate("BUG-001")
        assert CanonicalIdService.validate("SPIKE-001")
        assert CanonicalIdService.validate("INCIDENT-001")
        assert CanonicalIdService.validate("RELEASE-001")
        assert CanonicalIdService.validate("SETUP-001")
        assert CanonicalIdService.validate("EVOLUTION-001")

    def test_large_numbers_accepted(self):
        """Números maiores aceitos (TASK-10000, FEATURE-9999, EPIC-1000, etc.)."""
        assert CanonicalIdService.validate("TASK-10000")
        assert CanonicalIdService.validate("TASK-999999")
        assert CanonicalIdService.validate("FEATURE-9999")
        assert CanonicalIdService.validate("EPIC-1000")
        assert CanonicalIdService.validate("STORY-50000")

    def test_insufficient_digits_rejected(self):
        """IDs com dígitos insuficientes são estritamente rejeitados."""
        # Epics, Features, Stories exigem >= 3 dígitos
        assert not CanonicalIdService.validate("EPIC-1")
        assert not CanonicalIdService.validate("EPIC-01")
        assert not CanonicalIdService.validate("FEATURE-1")
        assert not CanonicalIdService.validate("FEATURE-99")
        assert not CanonicalIdService.validate("STORY-1")
        assert not CanonicalIdService.validate("STORY-42")

        # Tasks exigem >= 4 dígitos
        assert not CanonicalIdService.validate("TASK-1")
        assert not CanonicalIdService.validate("TASK-01")
        assert not CanonicalIdService.validate("TASK-001")

    def test_legacy_feat_recognized_as_legacy_feature(self):
        """FEAT-001 reconhecido como legacy Feature."""
        assert CanonicalIdService.is_legacy_alias("FEAT-001")
        assert CanonicalIdService.normalize("FEAT-001") == "FEATURE-001"
        assert CanonicalIdService.infer_kind("FEAT-001") == WorkItemKind.FEATURE
        assert CanonicalIdService.to_legacy_alias("FEATURE-001") == "FEAT-001"

    def test_legacy_us_recognized_as_legacy_story(self):
        """US-001 reconhecido como legacy Story."""
        assert CanonicalIdService.is_legacy_alias("US-001")
        assert CanonicalIdService.normalize("US-001") == "STORY-001"
        assert CanonicalIdService.infer_kind("US-001") == WorkItemKind.STORY
        assert CanonicalIdService.to_legacy_alias("STORY-001") == "US-001"

    def test_legacy_tk_recognized_as_legacy_task(self):
        """TK-0001 reconhecido como legacy Task."""
        assert CanonicalIdService.is_legacy_alias("TK-0001")
        assert CanonicalIdService.normalize("TK-0001") == "TASK-0001"
        assert CanonicalIdService.infer_kind("TK-0001") == WorkItemKind.TASK
        assert CanonicalIdService.to_legacy_alias("TASK-0001") == "TK-0001"

    def test_other_legacy_aliases_recognized(self):
        """REL- e EVOL- normalizados apropriadamente."""
        assert CanonicalIdService.is_legacy_alias("REL-002")
        assert CanonicalIdService.normalize("REL-002") == "RELEASE-002"
        assert CanonicalIdService.normalize("EVOL-001") == "EVOLUTION-001"

    def test_canonical_creation_never_emits_feat(self):
        """Criação canônica NUNCA emite FEAT-."""
        formatted_str = CanonicalIdService.format_canonical_id("FEATURE", 1)
        assert formatted_str == "FEATURE-001"
        assert not formatted_str.startswith("FEAT-")

        formatted_enum = CanonicalIdService.format_canonical_id(WorkItemKind.FEATURE, 42)
        assert formatted_enum == "FEATURE-042"
        assert not formatted_enum.startswith("FEAT-")

    def test_canonical_creation_never_emits_us(self):
        """Criação canônica NUNCA emite US-."""
        formatted_str = CanonicalIdService.format_canonical_id("STORY", 1)
        assert formatted_str == "STORY-001"
        assert not formatted_str.startswith("US-")

        formatted_enum = CanonicalIdService.format_canonical_id(WorkItemKind.STORY, 99)
        assert formatted_enum == "STORY-099"
        assert not formatted_enum.startswith("US-")

    def test_canonical_creation_never_emits_tk(self):
        """Criação canônica NUNCA emite TK-."""
        formatted_str = CanonicalIdService.format_canonical_id("TASK", 1)
        assert formatted_str == "TASK-0001"
        assert not formatted_str.startswith("TK-")

        formatted_enum = CanonicalIdService.format_canonical_id(WorkItemKind.TASK, 1000)
        assert formatted_enum == "TASK-1000"
        assert not formatted_enum.startswith("TK-")

    def test_invalid_prefix_rejected(self):
        """Prefixos arbitrários e inválidos são rejeitados."""
        assert not CanonicalIdService.validate("INVALID-001")
        assert not CanonicalIdService.validate("DEMAND-001")
        assert not CanonicalIdService.validate("CARD-001")

        with pytest.raises(ValidationError, match="Cannot infer WorkItemKind"):
            CanonicalIdService.infer_kind("UNKNOWN-001")

    def test_empty_id_rejected(self):
        """IDs vazios, espaços em branco ou tipos nulos são rejeitados."""
        assert not CanonicalIdService.validate("")
        assert not CanonicalIdService.validate("   ")
        assert not CanonicalIdService.validate(None)  # type: ignore

        with pytest.raises(ValidationError, match="must be a non-empty string"):
            CanonicalIdService.normalize("")

        with pytest.raises(ValidationError, match="must be a non-empty string"):
            CanonicalIdService.infer_kind("")

    def test_invalid_sequence_number_rejected(self):
        """Sequence number < 1 na formatação é rejeitado."""
        with pytest.raises(ValidationError, match="Sequence number must be >= 1"):
            CanonicalIdService.format_canonical_id("TASK", 0)

        with pytest.raises(ValidationError, match="Sequence number must be >= 1"):
            CanonicalIdService.format_canonical_id("FEATURE", -5)

    def test_unsupported_kind_formatting_rejected(self):
        """Formatação para tipos não suportados levanta ValidationError."""
        with pytest.raises(ValidationError, match="Unsupported WorkItem kind"):
            CanonicalIdService.format_canonical_id("NON_EXISTENT_KIND", 1)

    def test_normalization_idempotency(self):
        """Normalização em ID já canônico é estritamente idempotente."""
        assert CanonicalIdService.normalize("EPIC-001") == "EPIC-001"
        assert CanonicalIdService.normalize("FEATURE-001") == "FEATURE-001"
        assert CanonicalIdService.normalize("STORY-001") == "STORY-001"
        assert CanonicalIdService.normalize("TASK-0001") == "TASK-0001"

    def test_permissive_validation_for_legacy_demands(self):
        """validate_permissive aceita legados para leitura e compatibilidade."""
        assert CanonicalIdService.validate_permissive("FEAT-001")
        assert CanonicalIdService.validate_permissive("US-001")
        assert CanonicalIdService.validate_permissive("TK-0001")
        assert CanonicalIdService.validate_permissive("EPIC-001")
        assert CanonicalIdService.validate_permissive("FEATURE-001")
        assert not CanonicalIdService.validate_permissive("MALFORMED$$$")
