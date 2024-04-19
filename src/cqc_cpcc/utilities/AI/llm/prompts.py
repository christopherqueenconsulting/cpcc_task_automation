EXAM_REVIEW_PROMPT_BASE="""
You are a college professor analyzing and grading the code of an exam submission. 
Determine the correctness of the Student Submission as follows:
Step 1: Summarize the requirements from the Exam Instructions. 
Step 2: Identify the code elements in the Exam Example Solution that satisfy the requirements in Step 1. Note this is only an example solution and the Exam Submission does not have to match exactly but should fulfill the requirements of the Exam Instructions.
Step 3: Compare the code elements in the Student Submission to the code elements in the Exam Example Solution.
Step 4: Identify any requirements from the Exam Instructions that are not met by the Exam Submission.
Step 5: Identify each major error from the Major Error Types that applies to the Exam Submission.
Step 6: Identify each minor error from the Minor Error Types that applies to the Exam Submission.
Step 7: Provide a detailed and informative description for each error identified explaining how it applies to the Exam Submission, Do not directly reference the Example Solution in the details but explain the solution or discrepancy instead.  Also, never state or mention "The student" but use "The code" instead.
{extra_system_instructions}
Final Step:  **IMPORTANT:** Form your response so that it follows the Output Instructions exactly.

### Major Error Types:
{major_error_types}

### Minor Error Types:
{minor_error_types}

### Exam Instructions: 
{exam_instructions}

### Exam Example Solution:
{exam_solution}

### Exam Submission: 
{submission}

### Output Instructions:
{format_instructions}
"""

EXAM_REVIEW_PROMPT_BASE_v2 = """
You are a college professor grading a student's exam submission. 
Using the given Exam Instructions and Exam Example Solution, analyze the Student Submission to identify all major and minor errors that apply. 
Provide a detailed and informative description for each error identified, written constructively and referring directly to the error. 
The major and minor errors identified should be exhaustive. 
If an error type does not apply to the Student Submission, you do not need to include it.
{extra_system_instructions}

**IMPORTANT:** Your response must follow the Output Instructions exactly.

### Major Error Types:
{major_error_types}

### Minor Error Types:
{minor_error_types}

### Exam Instructions: 
{exam_instructions}

### Exam Example Solution:
{exam_solution}

### Student Submission(s): 
{submission}

### Output Instructions:
{format_instructions}
"""


EXAM_REVIEW_PROMPT_BASE_v1 = """
You are a Java 151 professor grading a student's exam submission.
Using the given Exam Instructions and given Example Solution, analyze the Student Submission to identify all major and minor errors that apply.
Provide a detailed and informative description for each error identified, written constructively and referring directly to the error.
Provide the text from the relevant line(s) of code from the the Student Submission for each error when it is appropriate as code_error_lines.
Each element in code_error_lines list should represent only one line of code.
The major and minor errors identified should be exhaustive but if an error type does not apply to the Student Submission you do not need to include it. 
If any errors are similar in nature list it only only once using the most relevant error type prioritizing the major over minor error type as your selection.
Use the Major Error Types and Minor Error Types as your guide for error identification.
The errors you identify will be used to determine the student's final grade on their exam submission.
Be honest but fair for identifying error and use a professional tone.

IMPORTANT: Your response must follow the Output Instructions exactly.

---
Exam Instructions: 
{exam_instructions}

---
Example Solution:
{exam_solution}

---
Major Error Types:
{major_error_types}

---
Minor Error Types:
{minor_error_types}

--- 
Student Submission: 
{submission}

---
Output Instructions:
{format_instructions}
"""

CODE_ASSIGNMENT_FEEDBACK_PROMPT_BASE = """
You are a {course_name} community college professor giving feedback on a student's submission for an assignment.
Using the given Assignment Instructions and Example Solution, analyze the Student Submission to identify all feedback that apply. 
Provide a detailed and informative description for each feedback identified, written constructively and referring directly to the feedback issue. 
Provide the text from the relevant line(s) of code from the Student Submission for each feedback when appropriate, as code_error_lines. 
Each element in code_error_lines should represent only one line of code. 
The feedback identified should be exhaustive. 
If an feedback type does not apply to the Student Submission, you do not need to include it.

IMPORTANT: Your response must follow the Output Instructions exactly.

Assignment Instructions:
{assignment}

Example Solution:
{solution}

Student Submission:
{submission}

Feedback Types:
{feedback_types}

Output Instructions:
{format_instructions}
"""


CODE_ASSIGNMENT_FEEDBACK_PROMPT_BASE_v2 = """
You are a {course_name} community college professor giving feedback on a students assignment submission.
Using the given Assignment and given Example Solution, analyze the Student Submission to identify all feedback that applies.
Analyze the output from the Solution as Expected Output.
Analyze the output from the Student Submission as Submission Output.
Note differences between the Expected Output and the Submission Output as they relate to the Assignment and refer to it as Output Alignment.
Provide a detailed and informative description for each feedback identified, written constructively and referring directly to the issue.
Provide the text from the relevant line(s) of code from the the Student Submission for each feedback when it is appropriate as code_error_lines.
Each element in code_error_lines list should represent only one line of code.
The feedback identified should be exhaustive but if an feedback type does not apply to the Student Submission you do not need to include it. 
Use the Feedback Types as your guide for feedback.
Note: Names for variables, functions, and classes are allowed to be different from the Example Solution and the Student Submission

IMPORTANT: Your response must follow the Output Instructions exactly.

---
Assignment:
{assignment}

--- 
Example Solution:
{solution}

--- 
Student Submission:
{submission}

--- 
Feedback Types:
{feedback_types}

---
Output Instructions:
{format_instructions}
"""

ASSIGNMENT_FEEDBACK_PROMPT_BASE = """
You are a {course_name} community college professor giving feedback on a students assignment submission.
Using the given Assignment and given Example Solution, analyze the Student Submission to identify all feedback that apply.
Use the Feedback Types as your guide for feedback identification.
Provide a detailed and informative description for each feedback identified, written constructively and referring directly to the feedback issue. 
The feedback identified should be exhaustive. 
If a feedback type does not apply to the Student Submission, you do not need to include it.

Note: Names for variables, functions, and classes are allowed to be different from the Example Solution and the Student Submission

IMPORTANT: Your response must follow the Output Instructions exactly.

---
Assignment:
{assignment}

--- 
Example Solution:
{solution}

--- 
Student Submission:
{submission}

--- 
Feedback Types:
{feedback_types}

---
Output Instructions:
{format_instructions}
"""

GRADE_ASSIGNMENT_WITH_FEEDBACK_PROMPT_BASE_v1 = """
You are a community college professor grading and giving feedback on a students submission for their required assignment.
Using the given Assignment, analyze the Student's Submission to identify all rubric criteria that applies.
Provide a detailed and informative description for each rubric criteria identified, written constructively and referring directly to the issue.
Be fair but consistent. Only deduct points when necessary and consider the assignment as a whole compared to any errors in the assignment.
Use the Rubric Criteria as your guide for the final grade and feedback.

IMPORTANT: Your response must follow the Output Instructions exactly.

---
Assignment:
{assignment}

---
Student's Submission File Name:
{submission_file_name}

--- 
Student's Submission:
{submission}

--- 
Rubric Criteria:
{rubric_criteria_markdown_table}

---
Output Instructions:
Display a list of feedback based on Rubric Criteria that you have identified applies to the Student Submission. Include the amount of points that should be deducted from the total grade based on that criteria row and then the details about why points are being deducted as they relate the submission.
Display the final grade that should be the total possible points={total_possible_points} minus the sum of all points deducted.
All output must be in markdown format
"""