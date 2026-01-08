#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric override system.

Tests cover:
1. Override precedence (UI overrides > base values)
2. Merging criteria, levels, and bands
3. Disabling criteria
4. Validation of merged rubrics
5. Compatibility checks
"""

import pytest
from pydantic import ValidationError

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import Rubric, Criterion, PerformanceLevel
from cqc_cpcc.rubric_overrides import (
    RubricOverrides,
    CriterionOverride,
    PerformanceLevelOverride,
    OverallBandOverride,
    merge_rubric_overrides,
    merge_criterion,
    merge_performance_level,
    merge_overall_band,
    validate_overrides_compatible,
)


@pytest.fixture
def base_rubric():
    """Fixture providing a base rubric for testing."""
    return get_rubric_by_id("default_100pt_rubric")


@pytest.mark.unit
class TestCriterionOverride:
    """Test criterion override merging."""
    
    def test_override_criterion_max_points(self, base_rubric):
        """Test overriding criterion max_points."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(max_points=30)
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 30  # Overridden
        
        # Total should reflect change
        # Original: 25+30+25+20=100, New: 30+30+25+20=105
        assert merged.total_points_possible == 105
    
    def test_override_criterion_name(self, base_rubric):
        """Test overriding criterion name."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(name="New Name for Understanding")
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.name == "New Name for Understanding"
    
    def test_disable_criterion(self, base_rubric):
        """Test disabling a criterion."""
        overrides = RubricOverrides(
            criterion_overrides={
                "style": CriterionOverride(enabled=False)
            },
            # Need to adjust ALL overall bands when total changes
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=70, score_max=80),
                "Proficient": OverallBandOverride(score_min=55, score_max=69),
                "Developing": OverallBandOverride(score_min=40, score_max=54),
                "Beginning": OverallBandOverride(score_min=0, score_max=39),
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        style = next(c for c in merged.criteria if c.criterion_id == "style")
        assert style.enabled is False
        
        # Total should exclude disabled criterion (original 100 - style 20 = 80)
        assert merged.total_points_possible == 80
    
    def test_multiple_criterion_overrides(self, base_rubric):
        """Test overriding multiple criteria."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    max_points=30,
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=28, score_max=30),
                        "Proficient": PerformanceLevelOverride(score_min=22, score_max=27),
                        "Developing": PerformanceLevelOverride(score_min=15, score_max=21),
                        "Beginning": PerformanceLevelOverride(score_min=0, score_max=14),
                    }
                ),
                "quality": CriterionOverride(
                    max_points=20,
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=18, score_max=20),
                        "Proficient": PerformanceLevelOverride(score_min=14, score_max=17),
                        "Developing": PerformanceLevelOverride(score_min=10, score_max=13),
                        "Beginning": PerformanceLevelOverride(score_min=0, score_max=9),
                    }
                ),
                "style": CriterionOverride(enabled=False),
            },
            # Adjust ALL bands for new total (30+30+20+0=80)
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=70, score_max=80),
                "Proficient": OverallBandOverride(score_min=55, score_max=69),
                "Developing": OverallBandOverride(score_min=40, score_max=54),
                "Beginning": OverallBandOverride(score_min=0, score_max=39),
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        # Check each override applied
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 30
        
        quality = next(c for c in merged.criteria if c.criterion_id == "quality")
        assert quality.max_points == 20
        
        style = next(c for c in merged.criteria if c.criterion_id == "style")
        assert style.enabled is False
        
        # Total: 30 (understanding) + 30 (completeness) + 20 (quality) + 0 (style disabled) = 80
        assert merged.total_points_possible == 80
    
    def test_no_overrides_returns_equivalent_rubric(self, base_rubric):
        """Test that empty overrides returns equivalent rubric."""
        overrides = RubricOverrides()
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        assert merged.rubric_id == base_rubric.rubric_id
        assert merged.total_points_possible == base_rubric.total_points_possible
        assert len(merged.criteria) == len(base_rubric.criteria)


@pytest.mark.unit
class TestPerformanceLevelOverride:
    """Test performance level override merging."""
    
    def test_override_level_score_range(self, base_rubric):
        """Test overriding level score ranges."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=24, score_max=25)
                    }
                )
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        exemplary = next(l for l in understanding.levels if l.label == "Exemplary")
        
        assert exemplary.score_min == 24  # Overridden
        assert exemplary.score_max == 25  # Overridden
    
    def test_override_level_description(self, base_rubric):
        """Test overriding level description."""
        new_desc = "Custom description for proficient level"
        overrides = RubricOverrides(
            criterion_overrides={
                "quality": CriterionOverride(
                    levels_overrides={
                        "Proficient": PerformanceLevelOverride(description=new_desc)
                    }
                )
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        quality = next(c for c in merged.criteria if c.criterion_id == "quality")
        proficient = next(l for l in quality.levels if l.label == "Proficient")
        
        assert proficient.description == new_desc
    
    def test_override_multiple_levels_in_criterion(self, base_rubric):
        """Test overriding multiple levels in same criterion."""
        overrides = RubricOverrides(
            criterion_overrides={
                "completeness": CriterionOverride(
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=28, score_max=30),
                        "Proficient": PerformanceLevelOverride(score_min=22, score_max=27),
                    }
                )
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        completeness = next(c for c in merged.criteria if c.criterion_id == "completeness")
        
        exemplary = next(l for l in completeness.levels if l.label == "Exemplary")
        assert exemplary.score_min == 28
        assert exemplary.score_max == 30
        
        proficient = next(l for l in completeness.levels if l.label == "Proficient")
        assert proficient.score_min == 22
        assert proficient.score_max == 27


@pytest.mark.unit
class TestOverallBandOverride:
    """Test overall band override merging."""
    
    def test_override_overall_band_range(self, base_rubric):
        """Test overriding overall band score range."""
        overrides = RubricOverrides(
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=92, score_max=100)
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        exemplary = next(b for b in merged.overall_bands if b.label == "Exemplary")
        assert exemplary.score_min == 92  # Overridden
        assert exemplary.score_max == 100


@pytest.mark.unit
class TestRubricMetadataOverride:
    """Test overriding rubric metadata."""
    
    def test_override_rubric_title(self, base_rubric):
        """Test overriding rubric title."""
        overrides = RubricOverrides(
            title_override="Custom Rubric Title"
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        assert merged.title == "Custom Rubric Title"
    
    def test_override_rubric_description(self, base_rubric):
        """Test overriding rubric description."""
        overrides = RubricOverrides(
            description_override="Custom description for this rubric"
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        assert merged.description == "Custom description for this rubric"


@pytest.mark.unit
class TestValidationErrors:
    """Test that invalid overrides are rejected."""
    
    def test_level_exceeding_max_points_fails(self, base_rubric):
        """Test that level exceeding criterion max_points fails."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=0, score_max=30)  # Exceeds 25
                    }
                )
            }
        )
        
        with pytest.raises(ValueError) as exc_info:
            merge_rubric_overrides(base_rubric, overrides)
        
        assert "exceeds" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()
    
    def test_overall_band_exceeding_total_fails(self, base_rubric):
        """Test that band exceeding total_points_possible fails."""
        overrides = RubricOverrides(
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=0, score_max=150)  # Exceeds 100
            }
        )
        
        with pytest.raises(ValueError) as exc_info:
            merge_rubric_overrides(base_rubric, overrides)
        
        assert "exceeds" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()
    
    def test_invalid_score_range_fails(self):
        """Test that invalid score range (min > max) is caught during merge."""
        # PerformanceLevelOverride doesn't validate range by itself (fields are optional)
        # but when merged with base level, Pydantic validation will catch it
        from cqc_cpcc.rubric_models import PerformanceLevel
        
        base_level = PerformanceLevel(
            label="Test", score_min=0, score_max=25, description="Test"
        )
        override = PerformanceLevelOverride(score_min=20, score_max=10)
        
        with pytest.raises(ValidationError):
            merge_performance_level(base_level, override)


@pytest.mark.unit
class TestOverridePrecedence:
    """Test override precedence rules."""
    
    def test_ui_override_takes_precedence(self, base_rubric):
        """Test that UI override always wins over base value."""
        # Base has max_points=25, reducing to 15 changes total from 100 to 90
        # Need to adjust both levels AND overall bands
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    max_points=15,
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=14, score_max=15),
                        "Proficient": PerformanceLevelOverride(score_min=10, score_max=13),
                        "Developing": PerformanceLevelOverride(score_min=6, score_max=9),
                        "Beginning": PerformanceLevelOverride(score_min=0, score_max=5),
                    }
                )
            },
            # Adjust overall bands to new total (15+30+25+20=90)
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=80, score_max=90),
                "Proficient": OverallBandOverride(score_min=65, score_max=79),
                "Developing": OverallBandOverride(score_min=50, score_max=64),
                "Beginning": OverallBandOverride(score_min=0, score_max=49),
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 15  # UI override wins
    
    def test_partial_override_keeps_other_fields(self, base_rubric):
        """Test that partial override keeps non-overridden fields."""
        original_understanding = next(
            c for c in base_rubric.criteria if c.criterion_id == "understanding"
        )
        original_name = original_understanding.name
        
        # Only override max_points
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(max_points=30)
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 30  # Overridden
        assert understanding.name == original_name  # Kept from base


@pytest.mark.unit
class TestValidateOverridesCompatible:
    """Test compatibility validation between overrides and base rubric."""
    
    def test_valid_overrides_pass(self, base_rubric):
        """Test that valid overrides pass compatibility check."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(max_points=30)
            }
        )
        
        is_valid, errors = validate_overrides_compatible(base_rubric, overrides)
        
        assert is_valid is True
        assert len(errors) == 0
    
    def test_nonexistent_criterion_fails(self, base_rubric):
        """Test that overriding nonexistent criterion fails."""
        overrides = RubricOverrides(
            criterion_overrides={
                "nonexistent_criterion": CriterionOverride(max_points=10)
            }
        )
        
        is_valid, errors = validate_overrides_compatible(base_rubric, overrides)
        
        assert is_valid is False
        assert len(errors) > 0
        assert "not found" in errors[0].lower()
    
    def test_nonexistent_level_fails(self, base_rubric):
        """Test that overriding nonexistent level fails."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    levels_overrides={
                        "NonexistentLevel": PerformanceLevelOverride(score_min=0, score_max=10)
                    }
                )
            }
        )
        
        is_valid, errors = validate_overrides_compatible(base_rubric, overrides)
        
        assert is_valid is False
        assert len(errors) > 0
        assert "not found" in errors[0].lower()
    
    def test_nonexistent_band_fails(self, base_rubric):
        """Test that overriding nonexistent band fails."""
        overrides = RubricOverrides(
            overall_bands_overrides={
                "NonexistentBand": OverallBandOverride(score_min=0, score_max=100)
            }
        )
        
        is_valid, errors = validate_overrides_compatible(base_rubric, overrides)
        
        assert is_valid is False
        assert len(errors) > 0
        assert "not found" in errors[0].lower()


@pytest.mark.unit
class TestComplexScenarios:
    """Test complex override scenarios."""
    
    def test_disable_all_but_one_criterion(self, base_rubric):
        """Test disabling all criteria except one."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(enabled=False),
                "completeness": CriterionOverride(enabled=False),
                "quality": CriterionOverride(enabled=False),
                # Keep "style" enabled
            },
            # Adjust overall bands for new total (20 points)
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=18, score_max=20),
                "Proficient": OverallBandOverride(score_min=14, score_max=17),
                "Developing": OverallBandOverride(score_min=10, score_max=13),
                "Beginning": OverallBandOverride(score_min=0, score_max=9),
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        # Only style should contribute to total (20 points)
        assert merged.total_points_possible == 20
    
    def test_combined_criterion_and_level_overrides(self, base_rubric):
        """Test combining criterion and level overrides."""
        overrides = RubricOverrides(
            criterion_overrides={
                "understanding": CriterionOverride(
                    max_points=30,
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=28, score_max=30)
                    }
                )
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 30
        
        exemplary = next(l for l in understanding.levels if l.label == "Exemplary")
        assert exemplary.score_min == 28
        assert exemplary.score_max == 30
    
    def test_all_override_types_combined(self, base_rubric):
        """Test combining all types of overrides."""
        # Total: 30+30+25+0=85
        overrides = RubricOverrides(
            title_override="Custom Title",
            criterion_overrides={
                "understanding": CriterionOverride(
                    max_points=30,
                    levels_overrides={
                        "Exemplary": PerformanceLevelOverride(score_min=28, score_max=30)
                    }
                ),
                "style": CriterionOverride(enabled=False),
            },
            overall_bands_overrides={
                "Exemplary": OverallBandOverride(score_min=75, score_max=85),
                "Proficient": OverallBandOverride(score_min=60, score_max=74),
                "Developing": OverallBandOverride(score_min=45, score_max=59),
                "Beginning": OverallBandOverride(score_min=0, score_max=44),
            }
        )
        
        merged = merge_rubric_overrides(base_rubric, overrides)
        
        # Check all overrides applied
        assert merged.title == "Custom Title"
        
        understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        assert understanding.max_points == 30
        
        style = next(c for c in merged.criteria if c.criterion_id == "style")
        assert style.enabled is False
        
        exemplary_band = next(b for b in merged.overall_bands if b.label == "Exemplary")
        assert exemplary_band.score_min == 75
        assert exemplary_band.score_max == 85  # Matches new total
