#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Configuration module for rubric-based grading system.

This module stores and loads rubrics and error definitions from JSON strings
embedded in configuration. This approach:
- Keeps configuration in code (no external files to maintain)
- Makes it easy to edit rubrics by changing JSON strings
- Provides clear validation errors when JSON is malformed
- Allows multiple rubrics to be defined and selected by ID

Configuration Structure:
- RUBRICS_JSON: JSON string containing dict of rubric_id -> rubric definition
- ERROR_DEFINITIONS_JSON: JSON string containing list of error definitions

Usage:
    >>> rubrics = load_rubrics_from_config()
    >>> exam_rubric = rubrics.get("java_exam_1")
    >>> errors = load_error_definitions_from_config()
"""

import json
from typing import Dict
from cqc_cpcc.rubric_models import Rubric, DetectedError
from cqc_cpcc.utilities.logger import logger


# ============================================================================
# RUBRICS CONFIGURATION (JSON STRING)
# ============================================================================

RUBRICS_JSON_v1_old = """{
    "default_100pt_rubric": {
        "rubric_id": "default_100pt_rubric",
        "rubric_version": "1.0",
        "title": "Default 100-Point Rubric",
        "description": "General purpose rubric for programming assignments",
        "course_ids": ["CSC151", "CSC152", "CSC251"],
        "criteria": [
            {
                "criterion_id": "understanding",
                "name": "Understanding & Correctness",
                "description": "Demonstrates understanding of concepts and correct implementation",
                "max_points": 25,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Complete understanding with excellent implementation; all requirements met with sophisticated approaches"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Good understanding with correct implementation; most requirements met with solid approaches"
                    },
                    {
                        "label": "Developing",
                        "score_min": 13,
                        "score_max": 17,
                        "description": "Partial understanding with some correct implementation; some requirements met but gaps remain"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 12,
                        "description": "Limited understanding; minimal correct implementation; many requirements unmet"
                    }
                ]
            },
            {
                "criterion_id": "completeness",
                "name": "Completeness / Requirements Coverage",
                "description": "Addresses all assignment requirements",
                "max_points": 30,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 27,
                        "score_max": 30,
                        "description": "All requirements fully met; includes enhancements beyond requirements"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 21,
                        "score_max": 26,
                        "description": "All major requirements met; minor requirements may have small gaps"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 20,
                        "description": "Most requirements addressed but significant gaps in coverage"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Many requirements missing or inadequately addressed"
                    }
                ]
            },
            {
                "criterion_id": "quality",
                "name": "Code Quality / Clarity",
                "description": "Code is clear, well-structured, and maintainable",
                "max_points": 25,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Exceptionally clear and well-structured; uses best practices consistently"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Clear and well-structured with minor areas for improvement"
                    },
                    {
                        "label": "Developing",
                        "score_min": 13,
                        "score_max": 17,
                        "description": "Readable but has structural issues or unclear sections"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 12,
                        "description": "Difficult to understand; poor structure or organization"
                    }
                ]
            },
            {
                "criterion_id": "style",
                "name": "Style / Conventions",
                "description": "Follows language conventions and coding standards",
                "max_points": 20,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 18,
                        "score_max": 20,
                        "description": "Consistently follows all conventions and standards"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 14,
                        "score_max": 17,
                        "description": "Follows most conventions with minor inconsistencies"
                    },
                    {
                        "label": "Developing",
                        "score_min": 10,
                        "score_max": 13,
                        "description": "Follows some conventions but has noticeable style issues"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 9,
                        "description": "Does not follow conventions; inconsistent or poor style"
                    }
                ]
            }
        ],
        "overall_bands": [
            {
                "label": "Exemplary",
                "score_min": 90,
                "score_max": 100
            },
            {
                "label": "Proficient",
                "score_min": 75,
                "score_max": 89
            },
            {
                "label": "Developing",
                "score_min": 60,
                "score_max": 74
            },
            {
                "label": "Beginning",
                "score_min": 0,
                "score_max": 59
            }
        ]
    },
    "csc151_java_exam_rubric": {
        "rubric_id": "csc151_java_exam_rubric",
        "rubric_version": "2.0",
        "title": "CSC 151 Java Exam Rubric (Brightspace-aligned)",
        "description": "Aligned to the official Brightspace Exam Grading Rubric for CSC151. Program Performance is scored strictly by error counts. Rule: Every 4 Minor Errors convert into 1 Major Error before scoring.",
        "course_ids": ["CSC151"],
        "criteria": [
            {
                "criterion_id": "program_performance",
                "name": "Program Performance",
                "description": "Apply Minor→Major conversion first. Every 4 Minor Errors become 1 Major Error (remainder stays Minor). Then select the score band.",
                "max_points": 100,
                "enabled": true,
                "scoring_mode": "error_count",
                "error_rules": {
                    "major_weight": 0,
                    "minor_weight": 0,
                    "error_conversion": {
                        "minor_to_major_ratio": 4
                    }
                },
                "levels": [
                    {
                        "label": "A+ (0 errors)",
                        "score_min": 96,
                        "score_max": 100,
                        "description": "Perfect - no errors detected"
                    },
                    {
                        "label": "A (1 minor error)",
                        "score_min": 91,
                        "score_max": 95,
                        "description": "Excellent - only 1 minor error"
                    },
                    {
                        "label": "A- (2 minor errors)",
                        "score_min": 86,
                        "score_max": 90,
                        "description": "Very good - 2 minor errors"
                    },
                    {
                        "label": "B (3 minor errors)",
                        "score_min": 81,
                        "score_max": 85,
                        "description": "Good - 3 minor errors"
                    },
                    {
                        "label": "B- (1 major error)",
                        "score_min": 71,
                        "score_max": 80,
                        "description": "Satisfactory - 1 major error"
                    },
                    {
                        "label": "C (2 major errors)",
                        "score_min": 61,
                        "score_max": 70,
                        "description": "Acceptable - 2 major errors"
                    },
                    {
                        "label": "D (3 major errors)",
                        "score_min": 16,
                        "score_max": 60,
                        "description": "Poor - 3 major errors"
                    },
                    {
                        "label": "F (4+ major errors)",
                        "score_min": 1,
                        "score_max": 15,
                        "description": "Failing - 4 or more major errors"
                    },
                    {
                        "label": "0 (Not submitted or incomplete)",
                        "score_min": 0,
                        "score_max": 0,
                        "description": "Not submitted or incomplete"
                    }
                ]
            }
        ]
    },
    "ai_assignment_reflection_rubric": {
        "rubric_id": "ai_assignment_reflection_rubric",
        "rubric_version": "1.0",
        "title": "AI Assignment Reflection Rubric",
        "description": "Rubric for grading AI tool reflection assignments. Uses level-band scoring where LLM selects performance levels and backend computes exact points.",
        "course_ids": ["CSC151", "CSC251"],
        "criteria": [
            {
                "criterion_id": "tool_description_usage",
                "name": "Tool Description & Usage",
                "description": "Demonstrates clear understanding of the AI tool's purpose, features, and how it was used in the assignment",
                "max_points": 25,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Comprehensive description of the AI tool with detailed explanation of features used; demonstrates sophisticated understanding of tool capabilities"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 19,
                        "score_max": 22,
                        "description": "Clear description of the AI tool and its main features; explains how the tool was used effectively"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 18,
                        "description": "Basic description of the AI tool; mentions some features but lacks detail on usage or effectiveness"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Minimal or unclear description of the AI tool; does not adequately explain usage or features"
                    }
                ]
            },
            {
                "criterion_id": "intelligence_analysis",
                "name": "Intelligence Analysis",
                "description": "Analyzes the intelligence and limitations of the AI tool; provides thoughtful reflection on its capabilities and constraints",
                "max_points": 30,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 27,
                        "score_max": 30,
                        "description": "Insightful and nuanced analysis of AI intelligence; identifies both capabilities and limitations with specific examples; demonstrates critical thinking"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 23,
                        "score_max": 26,
                        "description": "Good analysis of AI intelligence; identifies key capabilities and some limitations; provides relevant examples"
                    },
                    {
                        "label": "Developing",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Basic analysis of AI intelligence; mentions capabilities or limitations but lacks depth; few or generic examples"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 17,
                        "description": "Superficial or missing analysis; does not adequately explore AI capabilities or limitations"
                    }
                ]
            },
            {
                "criterion_id": "personal_goals_application",
                "name": "Personal Goals & Application",
                "description": "Connects AI tool usage to personal learning goals and future applications; reflects on learning and growth",
                "max_points": 25,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Thoughtful connection between AI tool and personal goals; detailed reflection on learning; specific plans for future application"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 19,
                        "score_max": 22,
                        "description": "Clear connection to personal goals; reflects on learning experience; mentions future applications"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 18,
                        "description": "Basic connection to goals; limited reflection; vague about future applications"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Minimal or no connection to personal goals; lacks reflection or future application plans"
                    }
                ]
            },
            {
                "criterion_id": "presentation_requirements",
                "name": "Presentation & Requirements",
                "description": "Meets assignment requirements including format, length, organization, and writing quality",
                "max_points": 20,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 18,
                        "score_max": 20,
                        "description": "Exceeds all requirements; well-organized; excellent writing quality; professional presentation"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 15,
                        "score_max": 17,
                        "description": "Meets all requirements; good organization; clear writing; appropriate presentation"
                    },
                    {
                        "label": "Developing",
                        "score_min": 12,
                        "score_max": 14,
                        "description": "Meets most requirements; some organizational or writing issues; adequate presentation"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 11,
                        "description": "Missing requirements; poor organization or writing quality; inadequate presentation"
                    }
                ]
            }
        ],
        "overall_bands": [
            {
                "label": "Exemplary",
                "score_min": 90,
                "score_max": 100
            },
            {
                "label": "Proficient",
                "score_min": 75,
                "score_max": 89
            },
            {
                "label": "Developing",
                "score_min": 60,
                "score_max": 74
            },
            {
                "label": "Beginning",
                "score_min": 0,
                "score_max": 59
            }
        ]
    }
}"""

RUBRICS_JSON = """{
  "default_100pt_rubric": {
    "rubric_id": "default_100pt_rubric",
    "rubric_version": "1.0",
    "title": "Default 100-Point Rubric",
    "description": "General purpose rubric for programming assignments",
    "course_ids": [
      "CSC151",
      "CSC152",
      "CSC251"
    ],
    "criteria": [
      {
        "criterion_id": "understanding",
        "name": "Understanding & Correctness",
        "description": "Demonstrates understanding of concepts and correct implementation",
        "max_points": 25,
        "enabled": true,
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Complete understanding with excellent implementation; all requirements met with sophisticated approaches"
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Good understanding with correct implementation; most requirements met with solid approaches"
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Partial understanding with some correct implementation; some requirements met but gaps remain"
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Limited understanding; minimal correct implementation; many requirements unmet"
          }
        ]
      },
      {
        "criterion_id": "completeness",
        "name": "Completeness / Requirements Coverage",
        "description": "Addresses all assignment requirements",
        "max_points": 30,
        "enabled": true,
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "All requirements fully met; includes enhancements beyond requirements"
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "All major requirements met; minor requirements may have small gaps"
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Most requirements addressed but significant gaps in coverage"
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Many requirements missing or inadequately addressed"
          }
        ]
      },
      {
        "criterion_id": "quality",
        "name": "Code Quality / Clarity",
        "description": "Code is clear, well-structured, and maintainable",
        "max_points": 25,
        "enabled": true,
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Exceptionally clear and well-structured; uses best practices consistently"
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Clear and well-structured with minor areas for improvement"
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Readable but has structural issues or unclear sections"
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Difficult to understand; poor structure or organization"
          }
        ]
      },
      {
        "criterion_id": "style",
        "name": "Style / Conventions",
        "description": "Follows language conventions and coding standards",
        "max_points": 20,
        "enabled": true,
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Consistently follows all conventions and standards"
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Follows most conventions with minor inconsistencies"
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Follows some conventions but has noticeable style issues"
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Does not follow conventions; inconsistent or poor style"
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc151_java_exam_rubric": {
    "rubric_id": "csc151_java_exam_rubric",
    "rubric_version": "2.0",
    "title": "CSC 151 Java Exam Rubric (Brightspace-aligned)",
    "description": "Aligned to the official Brightspace Exam Grading Rubric for CSC151. Program Performance is scored strictly by error counts. Rule: Every 4 Minor Errors convert into 1 Major Error before scoring.",
    "course_ids": [
      "CSC151"
    ],
    "criteria": [
      {
        "criterion_id": "program_performance",
        "name": "Program Performance",
        "description": "Apply Minor→Major conversion first. Every 4 Minor Errors become 1 Major Error (remainder stays Minor). Then select the score band.",
        "max_points": 100,
        "enabled": true,
        "scoring_mode": "error_count",
        "error_rules": {
          "major_weight": 0,
          "minor_weight": 0,
          "error_conversion": {
            "minor_to_major_ratio": 4
          }
        },
        "levels": [
          {
            "label": "A+ (0 errors)",
            "score_min": 96,
            "score_max": 100,
            "description": "Perfect - no errors detected"
          },
          {
            "label": "A (1 minor error)",
            "score_min": 91,
            "score_max": 95,
            "description": "Excellent - only 1 minor error"
          },
          {
            "label": "A- (2 minor errors)",
            "score_min": 86,
            "score_max": 90,
            "description": "Very good - 2 minor errors"
          },
          {
            "label": "B (3 minor errors)",
            "score_min": 81,
            "score_max": 85,
            "description": "Good - 3 minor errors"
          },
          {
            "label": "B- (1 major error)",
            "score_min": 71,
            "score_max": 80,
            "description": "Satisfactory - 1 major error"
          },
          {
            "label": "C (2 major errors)",
            "score_min": 61,
            "score_max": 70,
            "description": "Acceptable - 2 major errors"
          },
          {
            "label": "D (3 major errors)",
            "score_min": 16,
            "score_max": 60,
            "description": "Poor - 3 major errors"
          },
          {
            "label": "F (4+ major errors)",
            "score_min": 1,
            "score_max": 15,
            "description": "Failing - 4 or more major errors"
          },
          {
            "label": "0 (Not submitted or incomplete)",
            "score_min": 0,
            "score_max": 0,
            "description": "Not submitted or incomplete"
          }
        ]
      }
    ]
  },
  "csc113_week1_reflection_rubric": {
    "rubric_id": "csc113_week1_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 1 Reflection Rubric",
    "description": "Week 1 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "tool_description_usage",
        "name": "Tool Description & Usage",
        "description": "Identify an AI tool, explain how it's used, the task it accomplishes, and compare to doing it without AI.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Clearly identifies a specific AI tool and provides concrete examples of how it's used. Thoughtfully explains the task it accomplishes and offers a meaningful comparison to completing the task without AI assistance."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies an AI tool and describes its basic use. Explains the task it helps with and mentions what the task would be like without it, though the comparison may lack depth."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Names an AI tool but provides vague or limited description of actual usage. Comparison to non-AI approach is superficial or missing."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Tool identification unclear or not AI-based. Little description of usage or task comparison."
          }
        ]
      },
      {
        "criterion_id": "intelligence_analysis",
        "name": "Intelligence Analysis",
        "description": "Explain how the system works and discuss strengths and limitations with examples.",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Demonstrates understanding of what the system does behind the scenes with specific technical insights. Identifies both strengths and limitations with concrete examples from personal experience."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Shows reasonable understanding of how the system works. Identifies at least one strength and one limitation with supporting details."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 20,
            "description": "Makes general statements about how the system works without specifics. Mentions strengths or limitations but lacks supporting examples."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Minimal or inaccurate analysis of system functionality. Little or no discussion of strengths and limitations."
          }
        ]
      },
      {
        "criterion_id": "personal_goals_application",
        "name": "Personal Goals & Application",
        "description": "State learning goals for the course and connect them to professional application.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Articulates specific knowledge or skills to develop from the course and makes clear, thoughtful connections to how AI understanding will influence work, problem-solving, or decision-making in their field."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies relevant learning goals and makes reasonable connections to professional application, though connections may be somewhat general."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "States learning goals but connections to professional practice are vague or underdeveloped."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Learning goals unclear or missing. Little to no connection to professional application."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (250–400 words OR 2–3 minutes) and communicates clearly.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Meets format requirements (250-400 words OR 2-3 minutes). Content is well-organized, clear, and free from significant errors. Ideas flow logically."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Meets basic format requirements. Generally clear with minor organizational or mechanical issues that don't interfere with understanding."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Partially meets format requirements (too short/long). Organization or clarity issues make some parts difficult to follow. Multiple mechanical errors."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Does not meet format requirements. Significantly unclear or disorganized. Numerous mechanical errors interfere with comprehension."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week2_reflection_rubric": {
    "rubric_id": "csc113_week2_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 2 Reflection Rubric",
    "description": "Week 2 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals). Focus: learning approach identification, data pipeline analysis, bias-related failure mode, and presentation requirements.",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "learning_approach_identification",
        "name": "Learning Approach Identification",
        "description": "Correctly identifies the most likely learning approach (supervised, unsupervised, or reinforcement learning) and justifies the choice.",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Correctly identifies the most likely learning approach (supervised, unsupervised, or reinforcement learning) and provides clear, logical reasoning that demonstrates understanding of the approach's characteristics. Explanation shows ability to connect course concepts to real applications."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Identifies a plausible learning approach with reasonable justification. Reasoning demonstrates basic understanding of the learning type, though connections may be somewhat general."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Names a learning approach but reasoning is vague or shows incomplete understanding of how that approach works. May confuse key characteristics of different learning types."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Learning approach identification missing, incorrect, or shows significant misunderstanding of machine learning concepts."
          }
        ]
      },
      {
        "criterion_id": "data_pipeline_analysis",
        "name": "Data Pipeline Analysis",
        "description": "Analyzes the data type, volume, and realistic sources the system needs to function. Focus on data collection realities.",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Provides specific, thoughtful analysis of both data type and volume. Identifies realistic data sources and demonstrates understanding of what the system needs to function. Shows insight into the data collection process."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Identifies appropriate data types and considers volume. Suggests reasonable data sources, though analysis may lack some specificity or depth."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Mentions data types or sources but analysis is superficial. May overlook important data categories or provide limited consideration of volume and collection methods."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Data pipeline analysis unclear, missing, or shows significant gaps in understanding what data the system requires."
          }
        ]
      },
      {
        "criterion_id": "failure_mode_examination",
        "name": "Failure Mode Examination",
        "description": "Describes a concrete failure mode caused by biased or unrepresentative training data, including who is affected and how it manifests.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Describes a concrete, realistic failure mode resulting from biased or unrepresentative training data. Clearly identifies specific populations affected and explains how the failure would manifest. Demonstrates critical thinking about AI ethics and real-world impact."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies a plausible failure mode with some specificity. Mentions who might be affected and how, though the example may lack full detail or concrete consequences."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Describes a failure mode but remains abstract or generic. Limited discussion of affected populations or impacts. May focus on technical failures rather than bias-related issues."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Failure mode analysis missing, unclear, or does not address bias/representativeness issues. Little or no consideration of affected populations."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (250-400 words OR 2-3 minutes). Clear organization and writing quality.",
        "max_points": 15,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 14,
            "score_max": 15,
            "description": "Meets format requirements (250-400 words OR 2-3 minutes). Content is well-organized and communicates ideas clearly. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 11,
            "score_max": 13,
            "description": "Meets basic format requirements. Generally clear with minor issues that don't interfere with understanding."
          },
          {
            "label": "Developing",
            "score_min": 8,
            "score_max": 10,
            "description": "Partially meets format requirements. Organization or clarity issues present. Multiple errors that somewhat interfere with comprehension."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 7,
            "description": "Does not meet format requirements. Significantly unclear or disorganized. Errors substantially interfere with understanding."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week3_reflection_rubric": {
    "rubric_id": "csc113_week3_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 3 Reflection Rubric",
    "description": "Week 3 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "framing_specific_tension",
        "name": "Framing Specific Tension",
        "description": "Identifies a present-day ethical tension and competing values/interests.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Identifies a clear, concrete ethical tension currently present in the application. Articulates competing values or interests in conflict with specificity. Avoids abstract or future-oriented concerns, focusing on present-day impacts. Shows nuanced understanding that ethical issues involve genuine trade-offs, not simple right/wrong scenarios."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies a relevant ethical tension with reasonable clarity. Names competing values, though the framing may be somewhat general. Focuses on current rather than hypothetical concerns, with minor lapses into abstraction."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Identifies an ethical issue but framing is vague or overly broad. May conflate multiple tensions or struggle to articulate the specific values in conflict. May drift toward hypothetical future scenarios rather than current realities."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Ethical tension unclear, missing, or describes hypothetical rather than current concern. Little indication of competing values or interests. May present issue as simple problem rather than genuine tension."
          }
        ]
      },
      {
        "criterion_id": "mapping_stakeholders",
        "name": "Mapping Stakeholders",
        "description": "Analyzes multiple stakeholder groups and how impacts differ across groups.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Provides detailed analysis of multiple stakeholder groups with specific examples of how each is affected differently. Goes beyond obvious categories to consider non-users, workers, communities, or other affected parties. Clearly distinguishes between who benefits and who bears risks or costs, with concrete details about the nature of those benefits and harms."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies relevant stakeholder groups and describes differential impacts. Considers both beneficiaries and those who bear costs, though analysis may focus more heavily on users and companies than other affected parties. Provides reasonable specificity."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Lists stakeholders but analysis lacks depth or specificity about how different groups are affected. May treat all users as a monolithic group or overlook important stakeholder categories. Limited detail about the nature of benefits and harms."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Stakeholder analysis missing, extremely limited, or lacks meaningful differentiation. Fails to identify who benefits versus who bears costs, or provides only superficial treatment."
          }
        ]
      },
      {
        "criterion_id": "evaluating_trade_off",
        "name": "Evaluating Trade-off",
        "description": "Evaluates a proposed change intended to reduce harm by weighing its benefits against the costs, sacrifices, and resistance it would create. Considers who would be affected, what might be lost, and why the trade-off is difficult rather than assuming an easy or cost-free solution.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Proposes a concrete, actionable change that could reduce identified harm. Thoughtfully examines what the change would cost (financially, in convenience, in functionality, etc.) and identifies specific parties who might resist. Acknowledges the genuine difficulty of the trade-off rather than suggesting an easy solution. Shows sophisticated understanding that improvements often require sacrifice."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Proposes a reasonable change to reduce harm and considers associated costs or resistance. May identify trade-offs in general terms rather than with full specificity. Shows awareness that solutions involve compromise, though analysis could be deeper."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Proposes a change but presents it as straightforward fix rather than acknowledging costs. Limited consideration of who would resist or what would be sacrificed. May suggest vague rather than concrete actions."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Proposed change missing, unrealistic, or unrelated to identified harm. Little or no acknowledgment of costs, resistance, or trade-offs. May present naive 'win-win' solution that ignores real constraints."
          }
        ]
      },
      {
        "criterion_id": "reflecting_on_learning",
        "name": "Reflecting on Learning",
        "description": "Explains how thinking changed since Week 1–3 with specific references to learning experiences.",
        "max_points": 15,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 14,
            "score_max": 15,
            "description": "Identifies a specific assumption about AI held three weeks ago and clearly explains how that thinking has changed. Reflection is genuine and shows engagement with course content. Explains what caused the shift in thinking with concrete reference to learning experiences."
          },
          {
            "label": "Proficient",
            "score_min": 11,
            "score_max": 13,
            "description": "Describes a change in thinking about AI with reasonable specificity. Makes connection to course learning, though may be somewhat general about what caused the shift."
          },
          {
            "label": "Developing",
            "score_min": 8,
            "score_max": 10,
            "description": "Mentions changed thinking but remains vague about the original assumption or how it shifted. Limited detail about learning process or what influenced the change."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 7,
            "description": "Learning reflection missing, extremely general, or shows little evidence of actual conceptual change. May simply restate course content rather than describe personal shift in understanding."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (250–400 words OR 2–3 minutes) and communicates clearly.",
        "max_points": 10,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 9,
            "score_max": 10,
            "description": "Meets format requirements (250-400 words OR 2-3 minutes). Well-organized and communicates ideas clearly. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 7,
            "score_max": 8,
            "description": "Meets basic format requirements. Generally clear with minor issues that don't interfere with understanding."
          },
          {
            "label": "Developing",
            "score_min": 5,
            "score_max": 6,
            "description": "Partially meets format requirements. Organization or clarity issues present. Errors somewhat interfere with comprehension."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 4,
            "description": "Does not meet format requirements. Significantly unclear or disorganized. Errors substantially interfere with understanding."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week4_reflection_rubric": {
    "rubric_id": "csc113_week4_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 4 Reflection Rubric",
    "description": "Week 4 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "documentation_of_experiment",
        "name": "Documentation of Experiment",
        "description": "Includes three exact prompts (first attempt, revised, final) for the same task and shows progression.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Includes all three actual prompts exactly as submitted to the AI tool (instinctive first attempt, revised version, final iteration). Prompts show clear progression and address the same task. All prompts are complete and un-paraphrased. Task chosen is appropriate and relevant to field of study or work."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Includes three prompts with minor formatting issues or slight paraphrasing. Prompts address the same task and show progression, though iteration may be less systematic."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Missing one prompt, or prompts appear paraphrased/reconstructed rather than exact. Progression between prompts unclear or inconsistent. Task relevance questionable."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Missing multiple prompts, heavily paraphrased, or prompts do not address the same task. Little evidence of systematic experimentation."
          }
        ]
      },
      {
        "criterion_id": "analysis_of_output_changes",
        "name": "Analysis of Output Changes",
        "description": "Analyzes which prompt changes affected output quality and why (cause-effect).",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Provides specific, detailed analysis of which prompt elements affected output quality and how. Goes beyond generic observations to identify precise cause-effect relationships. Demonstrates genuine experimentation and observation. Shows understanding that different types of details serve different purposes."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Identifies several specific elements that changed output with reasonable cause-effect analysis. May include some generic observations but provides meaningful specificity about what worked. Shows evidence of careful observation."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Analysis remains somewhat general or vague. Limited cause-effect reasoning. Observations lack precision needed to demonstrate learning."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Analysis missing, extremely superficial, or shows little evidence of actual experimentation. Generic statements without specific observations about what changed and why."
          }
        ]
      },
      {
        "criterion_id": "limitation_identification",
        "name": "Limitation Identification",
        "description": "",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Describes a specific limitation encountered despite careful prompting. Provides concrete examples of what the tool couldn't do well. Offers thoughtful interpretation of what this failure reveals about how the system works, its training, or its design constraints. Shows critical thinking about AI capabilities and boundaries."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Identifies a genuine limitation with reasonable specificity. Discusses what the failure reveals about the system, though interpretation may be somewhat general. Shows awareness that AI tools have boundaries."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Mentions a limitation but description is vague or the limitation seems easily solvable with better prompting. Limited insight into what the failure reveals about system functionality. May conflate user error with system limitation."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Limitation missing, trivial, or unclear. Little or no interpretation of what the failure reveals. May claim the tool worked perfectly, suggesting insufficient experimentation or lack of critical engagement."
          }
        ]
      },
      {
        "criterion_id": "technical_connection",
        "name": "Technical Connection",
        "description": "",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Makes clear, specific connections between prompt structure and output quality using concepts from course content on training data and machine learning. Explains what the model is actually responding to with reference to how these systems are trained. Shows understanding of why certain prompt elements work (e.g., examples help because of pattern matching, specificity works because of how language models predict likely continuations). Applies technical knowledge to explain practical observations."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Connects prompt engineering observations to training data or machine learning concepts with reasonable accuracy. May be somewhat general but demonstrates understanding of the relationship between how models learn and how they respond to prompts.&nbsp;"
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Makes limited or superficial connections to technical concepts. May mention training data or machine learning but without clear links to prompt effectiveness. Shows incomplete understanding of why prompt structure matters from a technical perspective."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Technical connection missing, incorrect, or shows significant misunderstanding of machine learning concepts. Little or no explanation of what the model responds to or why prompt structure affects output."
          }
        ]
      },
      {
        "criterion_id": "presentatioin_and_requirements",
        "name": "Presentation & Requirements",
        "description": "",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (250-400 words plus prompts OR 2-3 minutes with prompts in separate file). Well-organized and clear. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Generally clear and organized."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. Some organization or clarity issues."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Significantly unclear or disorganized."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week5_reflection_rubric": {
    "rubric_id": "csc113_week5_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 5 Reflection Rubric",
    "description": "Week 5 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "technique_selection_justification",
        "name": "Technique Selection & Justification",
        "description": "Selects one specified technique and justifies why it fits the task better than alternatives.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Selects one of the specified techniques (chain-of-thought, few-shot examples, role/persona, structured output, or task decomposition) and provides clear, task-specific reasoning for why this technique fits better than alternatives. Justification shows understanding of what the technique does and how it addresses specific needs of the chosen task. Reasoning is presented before experimentation, not retrofitted afterward."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Selects an appropriate technique and offers reasonable justification. Explains why the technique fits the task, though reasoning may be somewhat general. Shows basic understanding of the technique's purpose."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Selects a technique but justification is vague or doesn't clearly explain why this technique suits this particular task better than others. May conflate multiple techniques or show incomplete understanding of what the technique does."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Technique selection missing, unclear, or not from the specified list. Little or no justification provided, or justification shows misunderstanding of the technique's function."
          }
        ]
      },
      {
        "criterion_id": "documentation_of_work",
        "name": "Documentation of Work",
        "description": "Includes complete prompts and enough output to evaluate results; shows iteration if it occurred.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Includes complete, un-paraphrased prompt(s) showing application of the chosen technique. Provides key portions of output sufficient to evaluate results. If iteration occurred, shows at least one revision with clear explanation of what prompted the change. Documentation allows reader to fully understand the experiment."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Includes prompts and output with minor omissions or formatting issues. Shows iteration if it occurred. Generally adequate documentation, though some details may be missing."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Prompts appear paraphrased or incomplete. Output excerpts too brief to evaluate results. If iteration occurred, changes not clearly explained. Documentation insufficient to fully understand the experiment."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Missing prompts, heavily paraphrased, or output not provided. Little evidence of actual experimentation. Cannot reconstruct what was attempted."
          }
        ]
      },
      {
        "criterion_id": "assessment_of_added_value",
        "name": "Assessment of Added Value",
        "description": "Compares structured technique vs straightforward prompting and evaluates effort vs benefit.",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Provides honest, critical comparison between the structured technique and a straightforward prompt approach. Clearly articulates whether the additional structure was worth the effort with specific evidence. If the technique didn't add much value, acknowledges this openly and analyzes why. If it did help, explains precisely how results improved. Shows mature judgment about trade-offs between effort and benefit. Avoids both uncritical enthusiasm and unwarranted dismissiveness."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Compares structured technique to simpler approach with reasonable specificity. Makes fair assessment of whether extra structure was worthwhile, though analysis could be more detailed. Shows some critical thinking about effort-benefit trade-off."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Comparison to simpler approach is vague or superficial. May claim technique helped without sufficient evidence, or may dismiss it without adequate consideration. Limited critical assessment of whether added structure justified the effort."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Assessment missing or shows little critical thinking. May automatically assume technique was beneficial without comparison, or may fail to consider what simpler approach would produce. No meaningful evaluation of effort-benefit trade-off."
          }
        ]
      },
      {
        "criterion_id": "use_case_definition",
        "name": "Use Case Definition",
        "description": "",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Articulates clear, practical decision rules for when to use this technique in actual work and when it would be unnecessary overhead. Based on experimental results, identifies specific task characteristics or conditions that warrant the technique. Shows strategic thinking about tool selection rather than one-size-fits-all approach. Decision rules are concrete enough to apply in future situations."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Defines reasonable use cases for the technique and acknowledges when it wouldn't be needed. Decision rules may be somewhat general but show practical judgment. Demonstrates awareness that technique isn't universally appropriate."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Use case definition vague or overly broad. May suggest technique is always useful or never useful rather than identifying specific conditions. Decision rules lack the specificity needed for practical application. Limited strategic thinking about when to invest effort in structured prompting."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Use case definition missing or shows poor judgment about when technique would be appropriate. No meaningful decision rules provided. May show lack of understanding about the effort-benefit calculation in real work contexts."
          }
        ]
      },
      {
        "criterion_id": "presentation_and_requirements",
        "name": "Presentation & Requirements",
        "description": "",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (250-400 words plus prompts OR 2-3 minutes with prompts in separate file). Well-organized and clear. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Generally clear and organized."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. Some organization or clarity issues."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Significantly unclear or disorganized."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week6_reflection_rubric": {
    "rubric_id": "csc113_week6_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 6 Reflection Rubric",
    "description": "Week 6 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals). Focus: recurring task selection, prompt design, testing, trade-offs, and presentation requirements.",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "recurring_task_identification",
        "name": "Recurring Task Identification",
        "description": "Identifies a genuinely recurring task from actual work or studies that is appropriate for AI assistance.",
        "max_points": 15,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 14,
            "score_max": 15,
            "description": "Identifies a genuinely recurring task from actual work or studies with clear professional or academic relevance. Task is specific enough to evaluate AI assistance meaningfully. Shows realistic understanding of where AI could plausibly help. Saves time, improves quality, or enables new capabilities."
          },
          {
            "label": "Proficient",
            "score_min": 11,
            "score_max": 13,
            "description": "Identifies an appropriate recurring task with reasonable specificity. Professional or academic relevance clear, though task description could be more detailed."
          },
          {
            "label": "Developing",
            "score_min": 8,
            "score_max": 10,
            "description": "Task identified but may be too vague, one-off rather than recurring, or questionable fit for AI assistance. Limited detail about context or why AI might help."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 7,
            "description": "Task unclear, not recurring, or shows poor judgment about AI applicability. May be hypothetical rather than drawn from actual work or studies."
          }
        ]
      },
      {
        "criterion_id": "design_of_approach",
        "name": "Design of Approach",
        "description": "Creates structured prompt(s) tailored to the task and justifies design choices using course techniques.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Creates complete, well-structured prompt(s) tailored specifically to the identified task. Explains structural choices with clear reference to techniques learned throughout the course. Justifies format decisions, level of detail, and technique selection based on task requirements. Shows strategic application of course concepts rather than generic prompting. Prompt demonstrates synthesis of learning from multiple weeks."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Creates appropriate prompt(s) with reasonable structure. Explains some design choices with reference to course concepts. Shows application of learned techniques, though connections could be more explicit or comprehensive."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Prompt provided but structural choices poorly explained or not clearly connected to course learning. May apply techniques superficially without clear rationale. Limited evidence of strategic design based on task needs."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Prompt missing, incomplete, or shows little evidence of applying course concepts. Minimal or absent explanation of design choices. Generic prompting without clear connection to learned techniques."
          }
        ]
      },
      {
        "criterion_id": "testing_against_reality",
        "name": "Testing Against Reality",
        "description": "Applies prompt and evaluates results honestly against professional standards with specific examples of what worked and what did not.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Applies prompt and provides honest, detailed evaluation of results. Clearly identifies where output met standards and where it fell short or failed. Makes realistic assessment of whether output could be used in actual work or requires significant revision. Shows intellectual honesty. Willing to acknowledge both successes and failures. Provides specific examples of what worked and what didn't."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Tests prompt and evaluates results with reasonable honesty. Identifies strengths and weaknesses of output. Makes fair assessment of usability, though evaluation could be more specific or critical."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Evaluation superficial or lacking in critical assessment. May avoid acknowledging failures or overstate successes. Limited specificity about what worked versus what required editing. Unclear whether output was actually tested."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Testing unclear or not conducted. Evaluation missing, dishonest, or shows poor judgment. No meaningful assessment of whether output meets professional standards or is usable in real work."
          }
        ]
      },
      {
        "criterion_id": "weighing_tradeoffs",
        "name": "Weighing Trade-offs",
        "description": "Considers time, quality, and risks to decide whether and how to incorporate AI for the task.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Considers multiple trade-off dimensions (time, quality, risks) and makes reasonable decision about AI incorporation. Analysis could be more comprehensive but shows practical thinking about costs and benefits."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Considers multiple trade-off dimensions (time, quality, risks) and makes reasonable decision about AI incorporation. Analysis could be more comprehensive but shows practical thinking about costs and benefits."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Trade-off analysis incomplete or superficial. May focus only on time savings without considering quality or risks. Decision about AI incorporation may lack clear justification or ignore important factors."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Trade-off analysis missing or shows poor judgment. Decision about AI use unjustified or unrealistic. Fails to consider important dimensions like quality, accuracy, or task-specific risks."
          }
        ]
      },
      {
        "criterion_id": "articulating_principles",
        "name": "Articulating Principles",
        "description": "Formulates a personal principle or guideline for when, why, and how to use generative AI based on lived experience, experimentation, and reflection across the first six weeks of the course. Emphasizes contextual judgment rather than rigid rules.",
        "max_points": 10,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 9,
            "score_max": 10,
            "description": "States clear personal guideline for when/how to use generative AI, grounded specifically in own experience and learning from the course. Principle is concrete enough to guide future decisions but not rigid. Shows reflection on six weeks of learning synthesized into practical wisdom. Avoids generic rules in favor of contextual judgment."
          },
          {
            "label": "Proficient",
            "score_min": 7,
            "score_max": 8,
            "description": "Articulates reasonable personal guideline with some grounding in experience. May be somewhat general but shows thoughtful reflection on appropriate AI use."
          },
          {
            "label": "Developing",
            "score_min": 5,
            "score_max": 6,
            "description": "Guideline vague or overly abstract. Limited connection to personal experience or course learning. May state obvious rules rather than developed principles."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 4,
            "description": "Principle missing, generic, or disconnected from experience. Shows little evidence of synthesis or reflection on course learning."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (300-450 words plus prompts OR 3-4 minutes with prompts in separate file). Integrates all required elements clearly.",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (300-450 words plus prompts OR 3-4 minutes with prompts in separate file). Well-organized, integrates all five required elements clearly. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Generally clear and complete."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. May be missing elements or fall short/exceed length significantly. Organization issues present."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Missing multiple required elements. Significantly unclear or disorganized."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week7_reflection_rubric": {
    "rubric_id": "csc113_week7_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 7 Reflection Rubric",
    "description": "Week 7 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "failure_documentation",
        "name": "Failure Documentation",
        "description": "",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Describes a specific, real instance where AI produced flawed output with concrete details about the task, what the AI produced, and precisely what was wrong (incorrect, misleading, incomplete, or subtly off). Provides enough context for reader to understand both the output and the flaw. Shows intellectual honesty—draws from actual experience rather than inventing convenient examples."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Documents a real AI failure with reasonable specificity. Describes the task and identifies what was wrong, though some details may be missing. Shows honest engagement with actual experience."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Describes an AI problem but lacks sufficient detail about what specifically was wrong or why. May be vague about the task or the nature of the flaw. Unclear whether this represents actual experience."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Failure documentation missing, hypothetical, or shows little understanding of what constitutes flawed AI output. Generic or invented example rather than real experience."
          }
        ]
      },
      {
        "criterion_id": "detection_process",
        "name": "Detectioin Process",
        "description": "",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Provides detailed walkthrough of how the error was detected. Identifies specific knowledge, skills, or instincts that revealed the flaw. Honest about whether detection was immediate or required closer examination. If the error was nearly missed, acknowledges this and reflects on why. Shows metacognitive awareness of own error-detection process. Demonstrates understanding that catching AI errors requires active verification, not passive acceptance."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Describes detection process with reasonable detail. Identifies what prompted recognition of the flaw. Shows awareness of the verification effort required, though description could be more specific about the knowledge or skills involved."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Detection process described but lacks detail or clarity about how the error was actually caught. May not identify specific knowledge that enabled detection. Limited reflection on the verification process or what made the error noticeable."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Detection process missing, vague, or shows little understanding of how errors are identified. May claim error was 'obvious' without explaining what made it so. No meaningful reflection on verification process."
          }
        ]
      },
      {
        "criterion_id": "verification_strategy_development",
        "name": "Verificatioin Strategy Development",
        "description": "",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Develops concrete, task-specific verification habits for evaluating AI outputs in their field. Goes well beyond generic advice like 'always double-check' to articulate what checking actually looks like for specific types of tasks. Strategies are practical, actionable, and grounded in understanding of where AI is likely to fail. Shows strategic thinking about verification as ongoing practice, not one-time check. May identify different verification approaches for different task types or risk levels."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Articulates reasonable verification strategies with some task specificity. Shows understanding that verification requires concrete actions beyond general caution. Strategies are somewhat actionable, though could be more detailed about implementation."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Verification strategy remains general or abstract. May rely on advice like 'double-check everything' without specifying what that means in practice. Limited connection to specific tasks or field contexts. Strategies may be vague about actual implementation."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Verification strategy missing, generic, or shows poor understanding of what effective verification requires. May suggest unrealistic or unhelpful approaches. No meaningful connection to practical application in specific contexts."
          }
        ]
      },
      {
        "criterion_id": "readiness_assessment",
        "name": "Readiness Assessment",
        "description": "Reflects on learning since Week 1 and shifts in understanding and approach.",
        "max_points": 15,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 14,
            "score_max": 15,
            "description": "Reflects thoughtfully on learning since Week 1, identifying significant shifts in understanding of AI or approach to working with these tools. Makes specific comparisons between earlier and current thinking. Shows genuine perspective change grounded in course experiences. Demonstrates integration of technical, ethical, and practical learning."
          },
          {
            "label": "Proficient",
            "score_min": 11,
            "score_max": 13,
            "description": "Identifies meaningful learning or perspective shifts since Week 1 with reasonable specificity. Shows evidence of changed thinking, though connections to course experiences could be clearer."
          },
          {
            "label": "Developing",
            "score_min": 8,
            "score_max": 10,
            "description": "Acknowledges some learning but description is vague or generic. Limited specific comparison between Week 1 and current understanding. May restate course content rather than describe personal perspective shift."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 7,
            "description": "Readiness assessment missing, superficial, or shows little evidence of perspective change. May claim dramatic transformation without supporting details, or may suggest minimal learning occurred."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (250–400 words OR 2–3 minutes) and addresses required elements clearly.",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (250-400 words OR 2-3 minutes). Well-organized, addresses all four required elements clearly. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Generally clear and complete."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. May be missing elements or fall short/exceed length. Organization issues present."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Missing multiple required elements. Significantly unclear or disorganized."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_week8_reflection_rubric": {
    "rubric_id": "csc113_week8_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Week 8 Reflection Rubric",
    "description": "Week 8 reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "sharpest_edge_identification",
        "name": "Sharpest Edge Identification",
        "description": "Identifies a specific capability developed and a realistic near-term application plan.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Identifies a specific, concrete capability developed during the course with clear confidence in its application. Describes a realistic situation in the coming month where this skill will be used, with enough detail to demonstrate genuine planning rather than hypothetical thinking. Shows accurate self-assessment of current strengths and readiness to apply learning."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Identifies a reasonable capability with some specificity. Describes a plausible upcoming situation for application, though details may be somewhat general. Shows reasonable self-assessment."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Capability identified but vague or overly broad. Upcoming situation lacks specificity or realism. May overestimate or underestimate readiness to apply learning."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Capability unclear or missing. No meaningful description of upcoming application. Shows poor self-assessment or little connection to course learning."
          }
        ]
      },
      {
        "criterion_id": "gap_identification",
        "name": "Gap Identification",
        "description": "Names a specific learning gap and demonstrates honest self-awareness.",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Names a specific topic, technique, or domain application that needs further development. Avoids generic statements like 'AI in general' in favor of precise identification of what remains unclear or underdeveloped. Shows honest self-awareness about limitations. Gap identified is realistic and addressable through continued learning."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Identifies a meaningful gap with reasonable specificity. Shows awareness of learning needs, though description could be more precise about what specifically needs work."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Gap identified but remains too general or vague. May say 'everything' or 'nothing' rather than identifying specific areas. Limited specificity about what's unclear or why."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Gap identification missing, unrealistic, or shows poor self-awareness. May claim complete mastery or total confusion rather than identifying specific development needs."
          }
        ]
      },
      {
        "criterion_id": "next_step_commitment",
        "name": "Next Step Commitment",
        "description": "Commits to a concrete, actionable next step within 30 days to address the gap.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Identifies a concrete, actionable step to be taken within 30 days to address the identified gap. Includes sufficient detail to enable actual follow-through (specific tutorial/course, particular project with scope, named tool, identified community, or defined use case). Shows realistic planning with appropriate scope. Action clearly connects to the gap identified and represents genuine commitment to continued learning."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Commits to a reasonable next step with some detail. Action is plausible and relates to identified gap, though planning could be more specific about implementation. Shows intent to continue learning."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Next step vague or lacks actionable detail. May say 'learn more about X' without specifying how. Unclear whether action is realistic within 30 days or meaningfully addresses gap. Limited evidence of genuine planning."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Next step missing, unrealistic, or disconnected from identified gap. No meaningful detail to enable follow-through. May be overly ambitious or trivially easy. Shows little evidence of commitment to continued learning."
          }
        ]
      },
      {
        "criterion_id": "teaching_experience",
        "name": "Teaching Experience",
        "description": "Describes a real teaching interaction and reflects on what teaching revealed about understanding.",
        "max_points": 30,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Provides clear description of what concept was taught, to whom, and how it was explained. Shows thoughtful choices based on audience and context. Describes specific questions or reactions from the learner that reveal genuine teaching interaction. Reflects meaningfully on what teaching revealed about own understanding. Demonstrates metacognitive awareness about learning through teaching."
          },
          {
            "label": "Proficient",
            "score_min": 21,
            "score_max": 26,
            "description": "Describes teaching experience with reasonable detail about concept, audience, and approach. Includes questions or reactions showing real interaction occurred. Reflects on teaching experience with some insight, though analysis of what it revealed could be deeper."
          },
          {
            "label": "Developing",
            "score_min": 15,
            "score_max": 20,
            "description": "Teaching experience described but lacks detail about concept, approach, or learner reactions. Minimal information about engagement. Limited reflection on what teaching revealed about own understanding. Unclear whether teaching actually occurred or is hypothetical."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 14,
            "description": "Teaching experience missing, clearly hypothetical, or described with insufficient detail. Little or no reflection on what the experience revealed. May claim teaching occurred without evidence of actual interaction. No meaningful insight into learning through teaching."
          }
        ]
      },
      {
        "criterion_id": "presentation_requirements",
        "name": "Presentation & Requirements",
        "description": "Meets format requirements (250–400 words OR 2–3 minutes) and addresses all four required elements clearly.",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (250-400 words OR 2-3 minutes). Well-organized, addresses all four required elements clearly. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Generally clear and complete."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. May be missing elements or fall short/exceed length. Organization issues present."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Missing multiple required elements. Significantly unclear or disorganized."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  },
  "csc113_final_reflection_rubric": {
    "rubric_id": "csc113_final_reflection_rubric",
    "rubric_version": "1.0",
    "title": "CSC-113 Final Reflection Rubric",
    "description": "Final reflection rubric for CSC-113 (Artificial Intelligence Fundamentals).",
    "course_ids": [
      "CSC113"
    ],
    "criteria": [
      {
        "criterion_id": "knowledge_growth",
        "name": "Knowledge Growth",
        "description": "Describes how AI understanding evolved throughout the course with specific examples and course connections.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Provides rich, specific description of how AI understanding has evolved throughout the course. Identifies multiple shifts in thinking with clear examples of what changed and why. Demonstrates progression from initial understanding to more sophisticated, nuanced perspective. References specific course concepts or moments that catalyzed changes in thinking. Shows integration of technical understanding (how AI learns), limitations (where it fails), and contextual awareness (when it's appropriate to use)."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Describes meaningful evolution in AI understanding with reasonable specificity. Identifies key learning moments and perspective shifts, though connections could be deeper. Shows evidence of changed thinking grounded in course experience. Covers multiple dimensions of AI understanding."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Acknowledges learning but description remains general or lacks specific examples of how understanding changed. May list topics covered rather than describe genuine perspective shifts. Limited depth in articulating what specifically evolved in thinking about AI."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Knowledge growth unclear or minimally described. May claim dramatic transformation without supporting evidence or suggest minimal learning occurred. Little specific connection to course content or experiences. Superficial treatment of how understanding developed."
          }
        ]
      },
      {
        "criterion_id": "practical_skills",
        "name": "Practical Skills",
        "description": "Describes prompting techniques mastered with concrete examples and when/why to apply them.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Describes specific prompting techniques mastered with concrete examples from actual use. Goes beyond naming techniques to demonstrate understanding of when and why to apply them. Examples show real application, not hypothetical scenarios. Articulates progression from basic to more sophisticated prompting approaches. Demonstrates strategic thinking about technique selection based on task requirements. Shows confidence in practical application while acknowledging remaining challenges."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "SEE SOURCE PDF (Final Reflection)"
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "SEE SOURCE PDF (Final Reflection)"
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "SEE SOURCE PDF (Final Reflection)"
          }
        ]
      },
      {
        "criterion_id": "ethical_considerations",
        "name": "Ethical Considerations",
        "description": "Reflects on responsible AI use and how ethical thinking evolved; ties ethics to future practice.",
        "max_points": 25,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 23,
            "score_max": 25,
            "description": "Provides thoughtful, nuanced reflection on how the course shaped thinking about responsible AI use. Goes beyond abstract principles to describe specific ethical tensions, trade-offs, and decision frameworks developed. Shows evolution from simpler to more complex ethical understanding. Identifies specific ways ethical considerations will influence future AI use. Demonstrates integration of ethical thinking with technical understanding, shows awareness that responsible use requires understanding both capabilities and limitations. Acknowledges complexity without retreating to simplistic rules."
          },
          {
            "label": "Proficient",
            "score_min": 18,
            "score_max": 22,
            "description": "Describes meaningful development in thinking about responsible AI use with reasonable depth. Identifies key ethical considerations and how perspective changed. Shows awareness of ethical complexities, though analysis could be more nuanced. Makes connections to future practice with some specificity."
          },
          {
            "label": "Developing",
            "score_min": 13,
            "score_max": 17,
            "description": "Addresses ethical considerations but remains general or abstract. May list ethical concerns without showing how thinking actually changed. Limited depth in describing decision frameworks or how ethics will influence practice. May present overly simplistic view of ethical AI use."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 12,
            "description": "Ethical reflection superficial, missing, or shows little engagement with course content on responsible AI use. May reduce ethics to simple rules without acknowledging complexity. No clear evidence of how thinking evolved or will influence future decisions."
          }
        ]
      },
      {
        "criterion_id": "future_applications",
        "name": "Future Applications",
        "description": "",
        "max_points": 20,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 18,
            "score_max": 20,
            "description": "Articulates specific, realistic plans for applying AI skills in professional or academic contexts. Describes particular situations, tasks, or workflows where AI will be integrated. Shows strategic thinking about where AI adds value versus where it's unnecessary or risky. Includes consideration of verification practices and ethical safeguards for intended uses. Plans are concrete enough to implement and grounded in self-aware assessment of current capabilities. Demonstrates integration of technical skills, practical techniques, and ethical judgment in planned applications."
          },
          {
            "label": "Proficient",
            "score_min": 14,
            "score_max": 17,
            "description": "Describes reasonable plans for applying AI skills with some specificity. Identifies relevant contexts for use and shows practical thinking about implementation. Plans are plausible though could be more detailed. Shows some consideration of appropriate use and verification."
          },
          {
            "label": "Developing",
            "score_min": 10,
            "score_max": 13,
            "description": "Future applications described but remain vague or overly general. May say 'I'll use AI for work' without specifying how, when, or with what safeguards. Limited strategic thinking about where AI is genuinely helpful versus unnecessary. Plans lack the specificity needed for actual implementation."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 9,
            "description": "Future applications missing, unrealistic, or show poor judgment about appropriate AI use. No meaningful connection between stated plans and demonstrated learning. May suggest indiscriminate use without consideration of context, limitations, or ethics."
          }
        ]
      },
      {
        "criterion_id": "presentation_and_requirements",
        "name": "Presentation & Requirements",
        "description": "",
        "max_points": 5,
        "enabled": true,
        "scoring_mode": "level_band",
        "points_strategy": "min",
        "levels": [
          {
            "label": "Exemplary",
            "score_min": 4,
            "score_max": 5,
            "description": "Meets all format requirements (500-750 words OR 3-5 minutes). Comprehensively addresses all four required elements. Well-organized with clear progression. Integrates elements naturally rather than treating as disconnected sections. Free from significant errors."
          },
          {
            "label": "Proficient",
            "score_min": 3,
            "score_max": 4,
            "description": "Meets basic requirements with minor issues. Addresses all elements, though integration could be stronger. Generally clear and organized."
          },
          {
            "label": "Developing",
            "score_min": 2,
            "score_max": 3,
            "description": "Partially meets requirements. May be missing depth in one or more elements, fall short/exceed length significantly, or have organizational issues that affect clarity."
          },
          {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 2,
            "description": "Does not meet format requirements. Missing required elements or addresses them superficially. Significantly unclear, disorganized, or incomplete."
          }
        ]
      }
    ],
    "overall_bands": [
      {
        "label": "Exemplary",
        "score_min": 90,
        "score_max": 100
      },
      {
        "label": "Proficient",
        "score_min": 75,
        "score_max": 89
      },
      {
        "label": "Developing",
        "score_min": 60,
        "score_max": 74
      },
      {
        "label": "Beginning",
        "score_min": 0,
        "score_max": 59
      }
    ]
  }
}"""


# ============================================================================
# ERROR DEFINITIONS CONFIGURATION (JSON STRING)
# ============================================================================

ERROR_DEFINITIONS_JSON = """[
    {
        "code": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
        "name": "Insufficient Documentation",
        "severity": "major",
        "description": "No documentation or insufficient amount of comments in the code"
    },
    {
        "code": "CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
        "name": "Sequence and Selection Error",
        "severity": "major",
        "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators"
    },
    {
        "code": "CSC_151_EXAM_2_METHOD_ERRORS",
        "name": "Method Errors",
        "severity": "major",
        "description": "Method errors in the code (passing the incorrect number of arguments, incorrect data types for arguments and parameter variables, or failing to include the data type of parameter variables in the method header)"
    },
    {
        "code": "CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR",
        "name": "Output Impact Error",
        "severity": "major",
        "description": "Errors that adversely impact the expected output, such as calculation errors or omissions"
    },
    {
        "code": "CSC_251_EXAM_1_INSUFFICIENT_DOCUMENTATION",
        "name": "Insufficient Documentation",
        "severity": "major",
        "description": "No documentation or insufficient amount of comments in the code"
    },
    {
        "code": "CSC_251_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
        "name": "Sequence and Selection Error",
        "severity": "major",
        "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators"
    },
    {
        "code": "CSC_251_EXAM_1_OUTPUT_IMPACT_ERROR",
        "name": "Output Impact Error",
        "severity": "major",
        "description": "Errors that adversely impact the expected output, such as calculation errors or omissions"
    },
    {
        "code": "CSC_251_EXAM_1_CONSTANTS_ERROR",
        "name": "Constants Error",
        "severity": "major",
        "description": "Constants are not properly declared or used"
    },
    {
        "code": "CSC_251_EXAM_1_DECIMAL_SCALE",
        "name": "Decimal Scale Error",
        "severity": "major",
        "description": "There are issues with the expected code output where the decimal scale is not correct and/or missing commas separators"
    },
    {
        "code": "CSC_251_EXAM_1_CURLY_BRACES_OMITTED",
        "name": "Curly Braces Omitted",
        "severity": "major",
        "description": "The code has omission of curly braces"
    },
    {
        "code": "CSC_251_EXAM_2_SECURITY_HOLES",
        "name": "Security Holes",
        "severity": "major",
        "description": "There are security holes in the code"
    },
    {
        "code": "CSC_251_EXAM_1_METHOD_ERRORS",
        "name": "Method Errors",
        "severity": "major",
        "description": "There are method errors (issues with parameters, return types, incorrect values, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_CLASS_DESIGN_ERRORS",
        "name": "Class Design Errors",
        "severity": "major",
        "description": "There are class design errors"
    },
    {
        "code": "CSC_251_EXAM_1_ARRAYLIST_ERRORS",
        "name": "ArrayList Errors",
        "severity": "major",
        "description": "There are errors involving ArrayList"
    },
    {
        "code": "CSC_251_EXAM_2_AGGREGATION_ERRORS",
        "name": "Aggregation Errors",
        "severity": "major",
        "description": "There are aggregation errors"
    },
    {
        "code": "CSC_151_EXAM_1_SYNTAX_ERROR",
        "name": "Syntax Error",
        "severity": "minor",
        "description": "There are syntax errors in the code"
    },
    {
        "code": "CSC_151_EXAM_1_NAMING_CONVENTION",
        "name": "Naming Convention Violation",
        "severity": "minor",
        "description": "Naming conventions are not followed"
    },
    {
        "code": "CSC_151_EXAM_1_CONSTANTS_ERROR",
        "name": "Constants Error",
        "severity": "minor",
        "description": "Constants are not properly declared or used"
    },
    {
        "code": "CSC_151_EXAM_1_INEFFICIENT_CODE",
        "name": "Inefficient Code",
        "severity": "minor",
        "description": "The code is inefficient and can be optimized"
    },
    {
        "code": "CSC_151_EXAM_1_OUTPUT_FORMATTING",
        "name": "Output Formatting Issues",
        "severity": "minor",
        "description": "There are issues with the expected code output formatting (spacing, decimal places, etc.)"
    },
    {
        "code": "CSC_151_EXAM_1_PROGRAMMING_STYLE",
        "name": "Programming Style Issues",
        "severity": "minor",
        "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)"
    },
    {
        "code": "CSC_151_EXAM_1_SCANNER_CLASS",
        "name": "Scanner Class Error",
        "severity": "minor",
        "description": "There are errors related to the use of the Scanner class"
    },
    {
        "code": "CSC_251_EXAM_1_SYNTAX_ERROR",
        "name": "Syntax Error",
        "severity": "minor",
        "description": "There are syntax errors in the code"
    },
    {
        "code": "CSC_251_EXAM_1_NAMING_CONVENTION",
        "name": "Naming Convention Violation",
        "severity": "minor",
        "description": "Naming conventions are not followed"
    },
    {
        "code": "CSC_251_EXAM_1_INEFFICIENT_CODE",
        "name": "Inefficient Code",
        "severity": "minor",
        "description": "The code is inefficient and can be optimized"
    },
    {
        "code": "CSC_251_EXAM_1_PROGRAMMING_STYLE",
        "name": "Programming Style Issues",
        "severity": "minor",
        "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_FILE_CLASS_NAME_MISMATCH",
        "name": "File/Class Name Mismatch",
        "severity": "minor",
        "description": "The filename and class container are not the same"
    },
    {
        "code": "CSC_251_EXAM_1_MINOR_FORMATTING",
        "name": "Minor Formatting Issues",
        "severity": "minor",
        "description": "There are formatting issues not matching Sample Input and Output (i.e spacing, missing dollar sign, not using print/println appropriately, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_STALE_DATA",
        "name": "Stale Data",
        "severity": "minor",
        "description": "There is stale data in classes"
    },
    {
        "code": "CSC_251_EXAM_1_UNUSED_VARIABLES",
        "name": "Unused Variables",
        "severity": "minor",
        "description": "There are variables/fields declared that are not used in the program"
    },
    {
        "code": "CSC_251_EXAM_1_INCORRECT_DATA_TYPE",
        "name": "Incorrect Data Type",
        "severity": "minor",
        "description": "The program has the incorrect data type(s) used"
    },
    {
        "code": "CSC_251_EXAM_1_DOES_NOT_COMPILE",
        "name": "Does Not Compile",
        "severity": "minor",
        "description": "The program does not compile"
    }
]"""


# ============================================================================
# LOADER FUNCTIONS
# ============================================================================

def load_rubrics_from_config() -> Dict[str, Rubric]:
    """Load rubrics from RUBRICS_JSON configuration string.
    
    Parses the JSON string, validates each rubric, and returns a dictionary
    mapping rubric_id to Rubric objects.
    
    Returns:
        Dictionary mapping rubric_id to validated Rubric objects
        
    Raises:
        ValueError: If JSON is invalid or rubric validation fails
        
    Example:
        >>> rubrics = load_rubrics_from_config()
        >>> exam_rubric = rubrics.get("default_100pt_rubric")
        >>> print(exam_rubric.total_points_possible)
        100
    """
    try:
        rubrics_data = json.loads(RUBRICS_JSON)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse RUBRICS_JSON: {e}")
        raise ValueError(f"Invalid JSON in RUBRICS_JSON: {e}")
    
    if not isinstance(rubrics_data, dict):
        raise ValueError("RUBRICS_JSON must be a JSON object (dict)")

    # Merge any legacy rubrics for backward compatibility.
    try:
        legacy_rubrics_data = json.loads(RUBRICS_JSON_v1_old)
        if isinstance(legacy_rubrics_data, dict):
            rubrics_data = {**legacy_rubrics_data, **rubrics_data}
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse RUBRICS_JSON_v1_old: {e}")
    
    rubrics = {}
    for rubric_id, rubric_dict in rubrics_data.items():
        try:
            rubric = Rubric.model_validate(rubric_dict)
            rubrics[rubric_id] = rubric
            logger.info(
                f"Loaded rubric '{rubric_id}': {rubric.title} "
                f"({rubric.total_points_possible} points, {len(rubric.criteria)} criteria)"
            )
        except Exception as e:
            logger.error(f"Failed to validate rubric '{rubric_id}': {e}")
            raise ValueError(f"Invalid rubric '{rubric_id}': {e}")
    
    if not rubrics:
        logger.warning("No rubrics found in RUBRICS_JSON")
    
    return rubrics


def load_error_definitions_from_config() -> list:
    """Load error definitions from ERROR_DEFINITIONS_JSON configuration string.
    
    Parses the JSON string and returns a list of error definition dictionaries.
    Note: Returns DetectedError objects from JSON, which are used as error definitions
    in the grading context. The JSON format uses DetectedError schema for simplicity.
    
    Returns:
        List of DetectedError objects that serve as error definitions
        
    Raises:
        ValueError: If JSON is invalid or error definition validation fails
        
    Example:
        >>> errors = load_error_definitions_from_config()
        >>> major_errors = [e for e in errors if e.severity == "major"]
        >>> print(len(major_errors))
    """
    try:
        errors_data = json.loads(ERROR_DEFINITIONS_JSON)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ERROR_DEFINITIONS_JSON: {e}")
        raise ValueError(f"Invalid JSON in ERROR_DEFINITIONS_JSON: {e}")
    
    if not isinstance(errors_data, list):
        raise ValueError("ERROR_DEFINITIONS_JSON must be a JSON array (list)")
    
    errors = []
    for i, error_dict in enumerate(errors_data):
        try:
            error = DetectedError.model_validate(error_dict)
            errors.append(error)
        except Exception as e:
            logger.error(f"Failed to validate error definition at index {i}: {e}")
            raise ValueError(f"Invalid error definition at index {i}: {e}")
    
    logger.info(f"Loaded {len(errors)} error definitions from config")
    major_count = sum(1 for e in errors if e.severity == "major")
    minor_count = sum(1 for e in errors if e.severity == "minor")
    logger.info(f"Error definitions: {major_count} major, {minor_count} minor")
    
    return errors


def get_rubric_by_id(rubric_id: str) -> Rubric:
    """Get a specific rubric by ID.
    
    Args:
        rubric_id: The rubric ID to retrieve
        
    Returns:
        The requested Rubric object
        
    Raises:
        ValueError: If rubric_id is not found
        
    Example:
        >>> rubric = get_rubric_by_id("default_100pt_rubric")
        >>> print(rubric.title)
        Default 100-Point Rubric
    """
    rubrics = load_rubrics_from_config()
    if rubric_id not in rubrics:
        available = ", ".join(rubrics.keys())
        raise ValueError(
            f"Rubric '{rubric_id}' not found. Available rubrics: {available}"
        )
    return rubrics[rubric_id]


def list_available_rubrics() -> list[str]:
    """List all available rubric IDs.
    
    Returns:
        List of rubric IDs available in configuration
        
    Example:
        >>> rubric_ids = list_available_rubrics()
        >>> print(rubric_ids)
        ['default_100pt_rubric']
    """
    rubrics = load_rubrics_from_config()
    return list(rubrics.keys())


def get_distinct_course_ids() -> list[str]:
    """Get list of distinct course IDs from all rubrics.
    
    Returns:
        Sorted list of unique course IDs across all rubrics
        
    Example:
        >>> course_ids = get_distinct_course_ids()
        >>> print(course_ids)
        ['CSC151', 'CSC152', 'CSC251']
    """
    rubrics = load_rubrics_from_config()
    course_ids_set = set()
    
    for rubric in rubrics.values():
        course_ids_set.update(rubric.course_ids)
    
    # Remove UNASSIGNED if there are other courses
    if len(course_ids_set) > 1 and "UNASSIGNED" in course_ids_set:
        course_ids_set.discard("UNASSIGNED")
    
    return sorted(list(course_ids_set))


def get_rubrics_for_course(course_id: str) -> Dict[str, Rubric]:
    """Get all rubrics applicable to a specific course.
    
    Args:
        course_id: Course identifier (e.g., "CSC151")
        
    Returns:
        Dictionary mapping rubric_id to Rubric objects for the specified course
        
    Example:
        >>> rubrics = get_rubrics_for_course("CSC151")
        >>> for rubric_id, rubric in rubrics.items():
        ...     print(f"{rubric_id}: {rubric.title}")
    """
    all_rubrics = load_rubrics_from_config()
    filtered_rubrics = {
        rubric_id: rubric
        for rubric_id, rubric in all_rubrics.items()
        if course_id in rubric.course_ids
    }
    
    logger.info(f"Found {len(filtered_rubrics)} rubrics for course '{course_id}'")
    return filtered_rubrics
