Feature: Executable quality gates

  @AC_QUALITY_1
  Scenario: Reject a gate with text-only quality claims
    Given a work item without executable evidence
    When gate G4 is evaluated
    Then the gate is rejected

  @AC_QUALITY_2
  Scenario: Accept a complete TDD cycle
    Given linked Red Green and Refactor evidence
    When the TDD evidence is validated
    Then the TDD cycle is approved
