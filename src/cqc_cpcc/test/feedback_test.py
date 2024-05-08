from datetime import datetime
from typing import Union

from cqc_cpcc.project_feedback import *
from cqc_cpcc.utilities.utils import read_file, read_files

ASSIGNMENT = """Program Description
   Write a program that outputs 'Hello World!' to the screen
   """

# NOTE: Make sure to use r""" to indicate raw string
SOLUTION = r"""import java.util.*;

public class HelloWorld
{
   // main method 
   public static void main(String[] args)
   {
       // Prints to the screen here
       System.out.println("Hello World!");
   }   
}
"""

# NOTE: Make sure to use r""" to indicate raw string
STUDENT_SUBMISSION = r"""import java.util.*;

public class StudentName_HelloWorld
{
   // main method 
   public static void main(String[] args)
   {
       // Prints to the screen here
       System.out.println("Hello World!");
   }   
}
"""

COURSE_NAME = "Java"


def give_feedback_on_assignments(assignment_name: str, assignment_instructions_file: str, assignment_solution_file: Union[str, List[str]],
                                 downloaded_exams_directory: str, wrap_code_in_markdown=True):
    # Get the assignment instructions
    assignment_instructions = read_file(assignment_instructions_file)

    # print("Assignment Instructions:\n%s" % assignment_instructions)

    # Get the assignment  solution
    assignment_solution = read_files(assignment_solution_file)

    # print("Assignment Solutions:\n%s" % assignment_solution)

    allowed_file_extensions = ("EmmaNova.java"
                               , ".docx"
                               )

    # Get all the files in the current working directory
    files = [file for file in os.listdir(downloaded_exams_directory) if file.endswith(allowed_file_extensions)]

    time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    # Loop through every file in directory and grade the assignment saved to a file
    for file in files:
        student_file_name, student_file_extension = os.path.splitext(file)
        student_file_path = downloaded_exams_directory + "/" + file
        student_submission = read_file(student_file_path)

        print("Generating Feedback for: %s" % student_file_name)
        feedback_guide = get_feedback_guide(assignment=assignment_instructions,
                                            solution=assignment_solution,
                                            student_submission=student_submission,
                                            course_name=COURSE_NAME,
                                            wrap_code_in_markdown=wrap_code_in_markdown)

        feedback = "\n".join([str(x) for x in feedback_guide.all_feedback])

        pre_feedback = "Good job submitting your assignment. "
        if len(feedback) > 0:
            pre_feedback = pre_feedback + "Here is my feedback:\n\n"
        else:
            pre_feedback = pre_feedback + "I find no issues with your submission. Keep up the good work!"
        post_feedback = "\n\n - " + FEEDBACK_SIGNATURE
        print("\n\n" + pre_feedback + feedback + post_feedback)

        file_path = f"./logs/{assignment_name}_{student_file_name}-{model}_temp({str(temperature)})-{time_stamp}.txt".replace(
            " ", "_")

        feedback_guide.save_feedback_to_docx(file_path.replace(".txt", ".docx"), pre_feedback, post_feedback)

        f = open(file_path, "w")
        f.write(pre_feedback + feedback + post_feedback)
        f.close()
        print("Feedback saved to : %s" % file_path)


if __name__ == '__main__':
    give_feedback_on_assignments(
        assignment_name="CSC151-N853-ProjectPart5",
        assignment_instructions_file="/Users/christopherqueen/Google Drive/CPCC/CPCC_Task_Automation/downloads/CSC151-N853/ProjectPart5/ProjectPart5_Instructions.docx",
        assignment_solution_file=["/Users/christopherqueen/Google Drive/CPCC/CPCC_Task_Automation/downloads/CSC151-N853/ProjectPart5/Project5_Solution.java",
                                  "/Users/christopherqueen/Google Drive/CPCC/CPCC_Task_Automation/downloads/CSC151-N853/ProjectPart5/HardwareLastname.java"],
        downloaded_exams_directory="/Users/christopherqueen/Google Drive/CPCC/CPCC_Task_Automation/downloads/CSC151-N853/ProjectPart5/submissions_all",
        wrap_code_in_markdown=True
    )
