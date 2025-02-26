import datetime as DT
import os
import tempfile
import zipfile
from pprint import pprint
from random import randint
from typing import Annotated

from langchain.chains.llm import LLMChain
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import StrictStr, Field
from pydantic.v1 import BaseModel

from cqc_cpcc.utilities.date import get_datetime
from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks


def test_1():
    today = DT.date.today()
    yesterday = today - DT.timedelta(days=2)
    week_ago = yesterday - DT.timedelta(days=7)
    middle_of_week = week_ago + DT.timedelta(days=3)

    print('Week agp: %s' % week_ago.strftime("%m-%d-%Y"))
    print('Middle of Week: %s' % middle_of_week.strftime("%m-%d-%Y"))
    print('Yesterday: %s' % yesterday.strftime("%m-%d-%Y"))

    proper_open_date = get_datetime('1/29/24')

    print('Proper Open Date: %s' % proper_open_date.strftime("%m-%d-%y"))

    if week_ago <= proper_open_date.date() <= yesterday:
        print("TRUE")
    else:
        print("FALSE")


def test3():
    wrapped_code = wrap_code_in_markdown_backticks("""
    import java.util.*;

public class Exam1_SPR24C
{
   // main method - aka the 'brain' method
   public static void main(String[] args)
   {
       Scanner input = new Scanner(System.in);
       
    }
}
    """)
    print(f"{wrapped_code}")


def extract_and_read_zip(file_path: str, accepted_file_types: list[str]) -> dict:
    unacceptable_file_prefixes = ['._']
    students_data = {}

    if file_path.endswith('.zip'):

        # Open the zip file
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            # Iterate over each file in the zip archive
            for file_info in zip_ref.infolist():
                # Extract the file name and directory name
                file_name = os.path.basename(file_info.filename)
                directory_name = os.path.dirname(file_info.filename)

                # Check if the directory name represents a student folder
                folder_name_delimiter = ' - '
                if directory_name and folder_name_delimiter in directory_name:
                    student_name = directory_name.split(folder_name_delimiter)[1]

                    # Check if the file has an accepted file type
                    if file_name.endswith(tuple(accepted_file_types)) and not file_name.startswith(
                            tuple(unacceptable_file_prefixes)):
                        # Read the file contents
                        with zip_ref.open(file_info.filename) as file:
                            # TODO: Change to modules on read file method
                            # file_contents = file.read().decode('utf-8')  # Assuming UTF-8 encoding
                            sub_file_name, sub_file_extension = os.path.splitext(file_name)
                            prefix = 'from_zip_' + str(randint(1000, 100000000)) + "_"
                            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix=prefix,
                                                                    suffix=sub_file_extension)
                            temp_file.write(file.read())
                            # file_contents = read_file(
                            #    temp_file.name)  # Reading this way incase it may be .docx or some other type we want to pre-process differently

                        # Store the file contents in the dictionary
                        if student_name not in students_data:
                            students_data[student_name] = {}
                        # students_data[student_name][file_name] = file_contents
                        students_data[student_name][file_name] = temp_file.name

    return students_data


def test4():
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "zip"]
    data = extract_and_read_zip(
        '/Users/christopherqueen/Google Drive/CPCC/CPCC_Task_Automation/downloads/CSC251-N850/exam1/submissions_all/Programming Exam 1 Download Apr 18, 2024 221 AM.zip',
        solution_accepted_file_types)
    pprint(data)


class TestPersonObject(BaseModel):
    name: Annotated[
        StrictStr,
        Field(description="The name of the person")
    ]
    details: Annotated[
        StrictStr,
        Field(description="The details about this person")
    ]


def test5():
    model_version = 'gpt-3.5-turbo-16k-0613'
    default_llm = ChatOpenAI(model=model_version)
    prompt = PromptTemplate(input_variables=["foo"], template="Who is {foo} and what are some details about them? ### Output Instructions: {format_instructions}")
    completion_chain = LLMChain(prompt=prompt, llm=default_llm)
    model = completion_chain.dict().get('llm').get('model')
    print("Model (from completion): %s" % model)
    print("Model (from llm): %s" % default_llm.dict().get('model'))

    parser = PydanticOutputParser(pydantic_object=TestPersonObject)
    format_instructions = parser.get_format_instructions()

    output = completion_chain.invoke({
        "foo": "Chris Queen",
        "format_instructions": format_instructions,
        "response_format": {"type": "json_object"}
    })

    print("\n\nOutput:")
    # pprint(output.content)
    pprint(output)

    final_output = parser.parse(output.get('text'))

    print("\n\nFinal Output:")
    # pprint(output.content)
    pprint(final_output)


if __name__ == '__main__':
    # test_1()
    # test3()
    # test4()
    test5()
