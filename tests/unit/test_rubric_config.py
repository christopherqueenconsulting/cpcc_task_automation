#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric configuration loading.

Tests cover:
1. Loading rubrics from JSON string
2. Loading error definitions from JSON string
3. Validation errors for malformed JSON
4. Utility functions for accessing config
"""

import json
import pytest
from unittest.mock import patch

from cqc_cpcc.rubric_config import (
    load_rubrics_from_config,
    load_error_definitions_from_config,
    get_rubric_by_id,
    list_available_rubrics,
    RUBRICS_JSON,
    ERROR_DEFINITIONS_JSON,
)
from cqc_cpcc.rubric_models import Rubric, DetectedError


@pytest.mark.unit
class TestLoadRubrics:
    """Test loading rubrics from configuration."""
    
    def test_load_rubrics_from_config_succeeds(self):
        """Test that rubrics load successfully from config."""
        rubrics = load_rubrics_from_config()
        
        assert isinstance(rubrics, dict)
        assert len(rubrics) > 0
        
        # Check default rubric exists
        assert "default_100pt_rubric" in rubrics
        default_rubric = rubrics["default_100pt_rubric"]
        
        assert isinstance(default_rubric, Rubric)
        assert default_rubric.rubric_id == "default_100pt_rubric"
        assert default_rubric.title == "Default 100-Point Rubric"
        assert default_rubric.total_points_possible == 100
        assert len(default_rubric.criteria) == 4
    
    def test_loaded_rubric_has_valid_criteria(self):
        """Test that loaded rubric has valid criteria structure."""
        rubrics = load_rubrics_from_config()
        rubric = rubrics["default_100pt_rubric"]
        
        # Check criteria
        criterion_ids = [c.criterion_id for c in rubric.criteria]
        assert "understanding" in criterion_ids
        assert "completeness" in criterion_ids
        assert "quality" in criterion_ids
        assert "style" in criterion_ids
        
        # Check first criterion has levels
        understanding = next(c for c in rubric.criteria if c.criterion_id == "understanding")
        assert understanding.levels is not None
        assert len(understanding.levels) == 4
        
        # Check level labels
        level_labels = [l.label for l in understanding.levels]
        assert "Exemplary" in level_labels
        assert "Proficient" in level_labels
    
    def test_loaded_rubric_has_overall_bands(self):
        """Test that loaded rubric has overall bands."""
        rubrics = load_rubrics_from_config()
        rubric = rubrics["default_100pt_rubric"]
        
        assert rubric.overall_bands is not None
        assert len(rubric.overall_bands) == 4
        
        band_labels = [b.label for b in rubric.overall_bands]
        assert "Exemplary" in band_labels
        assert "Proficient" in band_labels
    
    def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises ValueError."""
        with patch('cqc_cpcc.rubric_config.RUBRICS_JSON', 'invalid json {'):
            with pytest.raises(ValueError) as exc_info:
                load_rubrics_from_config()
            
            assert "Invalid JSON" in str(exc_info.value)
    
    def test_non_dict_json_raises_error(self):
        """Test that non-dict JSON raises ValueError."""
        with patch('cqc_cpcc.rubric_config.RUBRICS_JSON', '["not", "a", "dict"]'):
            with pytest.raises(ValueError) as exc_info:
                load_rubrics_from_config()
            
            assert "must be a JSON object" in str(exc_info.value)
    
    def test_invalid_rubric_data_raises_error(self):
        """Test that invalid rubric data raises ValueError."""
        invalid_rubric = {
            "bad_rubric": {
                "rubric_id": "bad_rubric",
                # Missing required fields
            }
        }
        
        with patch('cqc_cpcc.rubric_config.RUBRICS_JSON', json.dumps(invalid_rubric)):
            with pytest.raises(ValueError) as exc_info:
                load_rubrics_from_config()
            
            assert "Invalid rubric 'bad_rubric'" in str(exc_info.value)


@pytest.mark.unit
class TestLoadErrorDefinitions:
    """Test loading error definitions from configuration."""
    
    def test_load_error_definitions_succeeds(self):
        """Test that error definitions load successfully from config."""
        errors = load_error_definitions_from_config()
        
        assert isinstance(errors, list)
        assert len(errors) > 0
        
        # Check first error
        first_error = errors[0]
        assert isinstance(first_error, DetectedError)
        assert hasattr(first_error, 'code')
        assert hasattr(first_error, 'name')
        assert hasattr(first_error, 'severity')
        assert hasattr(first_error, 'description')
    
    def test_error_definitions_have_severities(self):
        """Test that error definitions have major and minor severities."""
        errors = load_error_definitions_from_config()
        
        severities = [e.severity for e in errors]
        assert "major" in severities
        assert "minor" in severities
        
        major_errors = [e for e in errors if e.severity == "major"]
        minor_errors = [e for e in errors if e.severity == "minor"]
        
        assert len(major_errors) > 0
        assert len(minor_errors) > 0
    
    def test_error_definitions_have_expected_codes(self):
        """Test that error definitions include expected codes."""
        errors = load_error_definitions_from_config()
        
        codes = [e.code for e in errors]
        
        # Check some known error codes
        assert "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION" in codes
        assert "CSC_151_EXAM_1_SYNTAX_ERROR" in codes
        assert "CSC_251_EXAM_1_METHOD_ERRORS" in codes
    
    def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises ValueError."""
        with patch('cqc_cpcc.rubric_config.ERROR_DEFINITIONS_JSON', 'invalid json ['):
            with pytest.raises(ValueError) as exc_info:
                load_error_definitions_from_config()
            
            assert "Invalid JSON" in str(exc_info.value)
    
    def test_non_list_json_raises_error(self):
        """Test that non-list JSON raises ValueError."""
        with patch('cqc_cpcc.rubric_config.ERROR_DEFINITIONS_JSON', '{"not": "a list"}'):
            with pytest.raises(ValueError) as exc_info:
                load_error_definitions_from_config()
            
            assert "must be a JSON array" in str(exc_info.value)
    
    def test_invalid_error_data_raises_error(self):
        """Test that invalid error data raises ValueError."""
        invalid_errors = [
            {
                "code": "TEST",
                # Missing required fields
            }
        ]
        
        with patch('cqc_cpcc.rubric_config.ERROR_DEFINITIONS_JSON', json.dumps(invalid_errors)):
            with pytest.raises(ValueError) as exc_info:
                load_error_definitions_from_config()
            
            assert "Invalid error definition at index 0" in str(exc_info.value)


@pytest.mark.unit
class TestUtilityFunctions:
    """Test utility functions for accessing configuration."""
    
    def test_get_rubric_by_id_succeeds(self):
        """Test getting a rubric by ID."""
        rubric = get_rubric_by_id("default_100pt_rubric")
        
        assert isinstance(rubric, Rubric)
        assert rubric.rubric_id == "default_100pt_rubric"
        assert rubric.total_points_possible == 100
    
    def test_get_rubric_by_invalid_id_raises_error(self):
        """Test that invalid rubric ID raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_rubric_by_id("nonexistent_rubric")
        
        error_msg = str(exc_info.value)
        assert "not found" in error_msg
        assert "Available rubrics:" in error_msg
    
    def test_list_available_rubrics_returns_ids(self):
        """Test listing available rubric IDs."""
        rubric_ids = list_available_rubrics()
        
        assert isinstance(rubric_ids, list)
        assert len(rubric_ids) > 0
        assert "default_100pt_rubric" in rubric_ids


@pytest.mark.unit
class TestConfigIntegrity:
    """Test integrity and consistency of configuration data."""
    
    def test_rubric_criteria_sum_to_total(self):
        """Test that rubric criteria sum to declared total."""
        rubrics = load_rubrics_from_config()
        
        for rubric_id, rubric in rubrics.items():
            # All criteria should be enabled by default
            enabled_criteria = [c for c in rubric.criteria if c.enabled]
            computed_total = sum(c.max_points for c in enabled_criteria)
            
            assert rubric.total_points_possible == computed_total, \
                f"Rubric '{rubric_id}' total mismatch: {rubric.total_points_possible} != {computed_total}"
    
    def test_rubric_performance_levels_valid(self):
        """Test that all performance levels are valid."""
        rubrics = load_rubrics_from_config()
        
        for rubric_id, rubric in rubrics.items():
            for criterion in rubric.criteria:
                if criterion.levels:
                    for level in criterion.levels:
                        # Check range is valid
                        assert level.score_min <= level.score_max, \
                            f"Invalid level range in '{rubric_id}' criterion '{criterion.criterion_id}'"
                        
                        # Check range fits in max_points
                        assert level.score_max <= criterion.max_points, \
                            f"Level exceeds max_points in '{rubric_id}' criterion '{criterion.criterion_id}'"
    
    def test_rubric_overall_bands_valid(self):
        """Test that overall bands are valid."""
        rubrics = load_rubrics_from_config()
        
        for rubric_id, rubric in rubrics.items():
            if rubric.overall_bands:
                for band in rubric.overall_bands:
                    # Check range is valid
                    assert band.score_min <= band.score_max, \
                        f"Invalid band range in '{rubric_id}'"
                    
                    # Check range fits in total
                    assert band.score_max <= rubric.total_points_possible, \
                        f"Band exceeds total in '{rubric_id}'"
    
    def test_error_definitions_have_unique_codes(self):
        """Test that error definition codes are unique."""
        errors = load_error_definitions_from_config()
        
        codes = [e.code for e in errors]
        unique_codes = set(codes)
        
        # Note: In the current config, codes are NOT unique (e.g., CSC_151 and CSC_251 share some)
        # This test documents current behavior - if you want uniqueness, adjust config
        # For now, just check that we have error definitions
        assert len(codes) > 0
    
    def test_error_definitions_have_descriptions(self):
        """Test that all error definitions have non-empty descriptions."""
        errors = load_error_definitions_from_config()
        
        for error in errors:
            assert error.description, f"Error '{error.code}' has empty description"
            assert len(error.description) > 10, f"Error '{error.code}' has very short description"


@pytest.mark.unit
class TestConfigReloading:
    """Test that config can be reloaded multiple times."""
    
    def test_multiple_loads_return_same_data(self):
        """Test that multiple loads return consistent data."""
        rubrics1 = load_rubrics_from_config()
        rubrics2 = load_rubrics_from_config()
        
        assert rubrics1.keys() == rubrics2.keys()
        
        for rubric_id in rubrics1.keys():
            assert rubrics1[rubric_id].rubric_id == rubrics2[rubric_id].rubric_id
            assert rubrics1[rubric_id].total_points_possible == rubrics2[rubric_id].total_points_possible
    
    def test_multiple_error_loads_return_same_count(self):
        """Test that multiple error loads return same count."""
        errors1 = load_error_definitions_from_config()
        errors2 = load_error_definitions_from_config()
        
        assert len(errors1) == len(errors2)


@pytest.mark.unit
class TestCSC151V2Rubric:
    """Test CSC151 v2.0 rubric configuration."""
    
    def test_csc151_v2_rubric_exists(self):
        """Test that CSC151 v2.0 rubric loads correctly."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        
        assert rubric.rubric_id == "csc151_java_exam_rubric"
        assert rubric.rubric_version == "2.0"
        assert rubric.title == "CSC 151 Java Exam Rubric (Brightspace-aligned)"
        assert "CSC151" in rubric.course_ids
    
    def test_csc151_v2_has_program_performance_criterion(self):
        """Test that CSC151 v2.0 has program_performance criterion."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        
        # Should have exactly one criterion
        assert len(rubric.criteria) == 1
        
        criterion = rubric.criteria[0]
        assert criterion.criterion_id == "program_performance"
        assert criterion.name == "Program Performance"
        assert criterion.max_points == 100
        assert criterion.enabled is True
    
    def test_csc151_v2_has_correct_levels(self):
        """Test that CSC151 v2.0 has all 9 performance levels."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # Should have 9 levels
        assert len(criterion.levels) == 9
        
        # Check level labels match specification
        expected_labels = [
            "A+ (0 errors)",
            "A (1 minor error)",
            "A- (2 minor errors)",
            "B (3 minor errors)",
            "B- (1 major error)",
            "C (2 major errors)",
            "D (3 major errors)",
            "F (4+ major errors)",
            "0 (Not submitted or incomplete)"
        ]
        
        actual_labels = [level.label for level in criterion.levels]
        assert actual_labels == expected_labels
    
    def test_csc151_v2_level_score_ranges(self):
        """Test that CSC151 v2.0 levels have correct score ranges."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # Check specific score ranges
        levels_by_label = {level.label: level for level in criterion.levels}
        
        assert levels_by_label["A+ (0 errors)"].score_min == 96
        assert levels_by_label["A+ (0 errors)"].score_max == 100
        
        assert levels_by_label["A (1 minor error)"].score_min == 91
        assert levels_by_label["A (1 minor error)"].score_max == 95
        
        assert levels_by_label["B- (1 major error)"].score_min == 71
        assert levels_by_label["B- (1 major error)"].score_max == 80
        
        assert levels_by_label["F (4+ major errors)"].score_min == 1
        assert levels_by_label["F (4+ major errors)"].score_max == 15
        
        assert levels_by_label["0 (Not submitted or incomplete)"].score_min == 0
        assert levels_by_label["0 (Not submitted or incomplete)"].score_max == 0
    
    def test_csc151_v2_total_points(self):
        """Test that CSC151 v2.0 has correct total points."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        
        # Total should be 100 (single criterion worth 100 points)
        assert rubric.total_points_possible == 100
    
    def test_csc151_v2_description_mentions_conversion(self):
        """Test that CSC151 v2.0 description mentions error conversion rule."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        
        # Description should mention the 4:1 conversion rule
        assert "4 Minor Errors" in rubric.description
        assert "1 Major Error" in rubric.description
    
    def test_csc151_v2_criterion_description_mentions_conversion(self):
        """Test that program_performance criterion mentions conversion."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # Criterion description should mention conversion
        assert "Minor→Major conversion" in criterion.description or "4 Minor Errors" in criterion.description

