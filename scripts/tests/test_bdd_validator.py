import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bdd_validator import validate_feature, validate_features


def test_valid_feature_requires_criterion_and_given_when_then(tmp_path):
    feature = tmp_path / "quality.feature"
    feature.write_text(
        """Feature: Quality gate

  Scenario: Reject missing evidence
    @AC-1
    Given a work item without evidence
    When the quality gate is evaluated
    Then the gate is rejected
""",
        encoding="utf-8",
    )
    assert validate_feature(feature)["approved"] is True


def test_feature_without_then_or_criterion_is_rejected(tmp_path):
    feature = tmp_path / "invalid.feature"
    feature.write_text(
        """Feature: Quality gate
  Scenario: Incomplete
    Given a work item
    When evaluated
""",
        encoding="utf-8",
    )
    result = validate_feature(feature)
    assert result["approved"] is False
    assert "Scenario 1 sem Then" in result["errors"]
    assert "Scenario 1 sem tag de critério" in result["errors"]


def test_empty_feature_directory_is_rejected(tmp_path):
    assert validate_features(tmp_path)["approved"] is False


def test_feature_without_header_or_scenario_is_rejected(tmp_path):
    feature = tmp_path / "empty.feature"
    feature.write_text("plain text\n", encoding="utf-8")
    result = validate_feature(feature)
    assert result["approved"] is False
    assert result["errors"] == ["Feature ausente", "Scenario ausente"]


def test_validate_features_aggregates_invalid_files(tmp_path):
    (tmp_path / "valid.feature").write_text(
        """Feature: Valid
  @AC-1
  Scenario: Complete
    Given a condition
    When an action occurs
    Then an outcome follows
""",
        encoding="utf-8",
    )
    (tmp_path / "invalid.feature").write_text("Feature: Invalid\n", encoding="utf-8")

    result = validate_features(tmp_path)

    assert result["approved"] is False
    assert len(result["features"]) == 2
