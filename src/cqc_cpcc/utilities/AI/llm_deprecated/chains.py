import json
from pprint import pprint
from typing import Type, TypeVar

from langchain_classic.output_parsers import RetryWithErrorOutputParser
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import BaseOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableSerializable
from langchain_core.runnables.utils import Output
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from cqc_cpcc.utilities.AI.llm_deprecated.llms import get_default_retry_model
from cqc_cpcc.utilities.AI.llm_deprecated.prompts import *
from cqc_cpcc.utilities.env_constants import RETRY_PARSER_MAX_RETRY, SHOW_ERROR_LINE_NUMBERS, DEBUG
from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser

T = TypeVar("T", bound=BaseModel)


def retry_output(output: Output, parser: BaseOutputParser, prompt: PromptTemplate, retry_model: str,
                 **prompt_args) -> T:
    final_output = output
    retry_llm = ChatOpenAI(temperature=0, model=retry_model, use_responses_api=True)
    retry_parser = RetryWithErrorOutputParser.from_llm(parser=parser, llm=retry_llm,
                                                       max_retries=RETRY_PARSER_MAX_RETRY
                                                       )
    try:
        prompt_value = prompt.format_prompt(**prompt_args)
        final_output = retry_parser.parse_with_prompt(output.content, prompt_value)
        # final_output = retry_parser.parse_with_prompt(output.get('text'), prompt_value)
    except OutputParserException as e:
        print("Exception During Retry Output: %s" % e)
        # if max_tries > 0:
        #    finalOutput = retryOutput(finalOutput, prompt, max_tries - 1, **prompt_args)
    return final_output


def generate_error_definitions(llm: BaseChatModel, pydantic_object: Type[T], major_error_type_list: list,
                               minor_error_type_list: list, exam_instructions: str, exam_solution: str,
                               student_submission: str) -> str:
    """ Returns a properly formatted error definitions object from LLM"""

    parser = CustomPydanticOutputParser(pydantic_object=pydantic_object,
                                        major_error_type_list=major_error_type_list,
                                        minor_error_type_list=minor_error_type_list)
    format_instructions = parser.get_format_instructions()

    extra_system_instructions = ""
    if SHOW_ERROR_LINE_NUMBERS:
        extra_system_instructions = """Provide the first 25 characters of the relevant line(s) of code from the Student Submission for each error when appropriate, as code_error_lines. 
        Each element in code_error_lines should represent only one line of code. 
        """

    prompt = PromptTemplate(
        # template_format="jinja2",
        input_variables=["submission"],
        partial_variables={
            "extra_system_instructions": extra_system_instructions,
            "exam_instructions": exam_instructions,
            "exam_solution": exam_solution,
            "format_instructions": format_instructions,
            "major_error_types": "\n\t".join(major_error_type_list),
            "minor_error_types": "\n\t".join(minor_error_type_list)
        },
        template=(
            EXAM_REVIEW_PROMPT_BASE
        ).strip(),
    )

    # prompt_value = prompt.format_prompt(exam_instructions=exam_instructions, exam_solution=exam_solution, submission=student_submission)
    # print("\n\nPrompt Value:")
    # pprint(prompt_value)
    # print("\n\n")

    llm.bind(
        response_format={"type": "json_object"}
    )

    # completion_chain = LLMChain(llm=llm, prompt=prompt)
    completion_chain = prompt | llm

    output = completion_chain.invoke({
        "submission": student_submission,
        "response_format": {"type": "json_object"}
    })

    # print("\n\nOutput:")
    # pprint(output.content)
    # pprint(output.get('text'))

    try:
        final_output = parser.parse(output.content)
        # final_output = parser.parse(output.get('text'))
    except Exception as e:
        print("\n\nException during parse:")
        print(e)
        # retry_model = get_llm_model_from_runnable_serializable(completion_chain)
        retry_model = get_default_retry_model()
        final_output = retry_output(output, parser, prompt,
                                    exam_instructions=exam_instructions,
                                    exam_solution=exam_solution,
                                    submission=student_submission,
                                    retry_model=retry_model
                                    )

    return final_output


def get_exam_error_definitions_completion_chain(_llm: BaseChatModel, pydantic_object: Type[T],
                                                major_error_type_list: list,
                                                minor_error_type_list: list, exam_instructions: str, exam_solution: str
                                                ) -> tuple[
    RunnableSerializable, CustomPydanticOutputParser, PromptTemplate]:
    """Returns a properly formatted error definitions object from LLM.
    
    DEPRECATED: This function uses LangChain chains for exam grading.
    For new code, use cqc_cpcc.utilities.AI.exam_grading_openai.grade_exam_submission instead.
    This function is kept for backward compatibility only.
    """

    parser = CustomPydanticOutputParser(pydantic_object=pydantic_object,
                                        major_error_type_list=major_error_type_list,
                                        minor_error_type_list=minor_error_type_list)

    format_instructions = parser.get_format_instructions()

    extra_system_instructions = ""
    if SHOW_ERROR_LINE_NUMBERS:
        extra_system_instructions = """Provide the first 25 characters of the relevant line(s) of code from the Exam Submission for each error when appropriate, as code_error_lines. 
    Each element in code_error_lines should represent only one line of code. 
    """

    prompt = PromptTemplate(
        # template_format="jinja2",
        input_variables=["submission"],
        partial_variables={
            "extra_system_instructions": extra_system_instructions,
            "exam_instructions": exam_instructions,
            "exam_solution": exam_solution,
            "format_instructions": format_instructions,
            "major_error_types": "- " + ("\n- ".join(major_error_type_list)),
            "minor_error_types": "- " + ("\n- ".join(minor_error_type_list))
        },
        template=(
            EXAM_REVIEW_PROMPT_BASE
        ).strip(),
    )

    # prompt = hub.pull("cqc/exam_review")

    # prompt_value = prompt.format_prompt( submission=student_submission)
    # print("\n\nPrompt Value:")
    # pprint(prompt_value)
    # print("\n\n")

    _llm.bind(
        response_format={"type": "json_object"}
    )

    _llm.with_structured_output(pydantic_object, method="json_schema", include_raw=False)

    completion_chain = prompt | _llm
    # completion_chain = LLMChain(llm=_llm, prompt=prompt)

    """
    # Bind the variables to the completion chain
    completion_chain.bind(extra_system_instructions=extra_system_instructions,
                          exam_instructions=exam_instructions,
                          exam_solution=exam_solution,
                          format_instructions=format_instructions,
                          major_error_types="- " + ("\n- ".join(major_error_type_list)),
                          minor_error_types="- " + ("\n- ".join(minor_error_type_list)))
    """

    return completion_chain, parser, prompt


async def get_exam_error_definition_from_completion_chain(student_submission: str,
                                                          completion_chain: RunnableSerializable,
                                                          parser: CustomPydanticOutputParser,
                                                          prompt: PromptTemplate,
                                                          callback: BaseCallbackHandler = None
                                                          ) -> T:
    """Get exam error definitions from completion chain.
    
    DEPRECATED: This function uses LangChain chains for exam grading.
    For new code, use cqc_cpcc.utilities.AI.exam_grading_openai.grade_exam_submission instead.
    This function is kept for backward compatibility only.
    """
    config = None
    if callback:
        config = {'callbacks': [callback]}

    output = await completion_chain.ainvoke({
        "submission": student_submission,
        "response_format": {"type": "json_object"}
    },
        config=config)

    if DEBUG:
        print("\n\nOutput:")
        pprint(output.content)

    # Defensive parse of the returned content
    content = output.content
    final_output = None

    # If it's already a JSON string
    if isinstance(content, str):
        try:
            final_output = parser.parse(content)
            if DEBUG:
                print("\n\nFinal Output (was already json string):")
                pprint(final_output)
        except Exception as e:
            print("\n\nException during parse (string):")
            print(e)
            retry_model = get_default_retry_model()
            final_output = retry_output(output, parser, prompt,
                                        submission=student_submission,
                                        retry_model=retry_model
                                        )

    # If the Responses API returned a dict already
    elif isinstance(content, dict):
        try:
            final_output = parser.parse(json.dumps(content))
            if DEBUG:
                print("\n\nFinal Output (already a dict):")
                pprint(final_output)
        except Exception as e:
            print("\n\nException during parse (dict):")
            print(e)
            retry_model = get_default_retry_model()
            final_output = retry_output(output, parser, prompt,
                                        submission=student_submission,
                                        retry_model=retry_model
                                        )

    # If the Responses API returned a list (common case: [{'type':'text','text':'{...}'}])
    elif isinstance(content, list):
        parsed_obj = None
        # look for a JSON string in common keys, or for a dict matching expected keys
        for item in content:
            if isinstance(item, dict):
                # direct structured dict (contains expected top-level keys)
                if any(k in item for k in ("all_major_errors", "all_minor_errors")):
                    parsed_obj = item
                    break
                # common text fields that may contain JSON
                for key in ("text", "content", "value", "data"):
                    if key in item and isinstance(item[key], str):
                        s = item[key].strip()
                        try:
                            parsed_obj = json.loads(s)
                            break
                        except Exception:
                            # not valid JSON, continue scanning
                            parsed_obj = None
                if parsed_obj is not None:
                    break

        if parsed_obj is not None:
            try:
                final_output = parser.parse(json.dumps(parsed_obj))
                if DEBUG:
                    print("\n\nFinal Output (parsed from list):")
                    pprint(final_output)
            except Exception as e:
                print("\n\nException during parse (from list):")
                print(e)
                retry_model = get_default_retry_model()
                final_output = retry_output(output, parser, prompt,
                                            submission=student_submission,
                                            retry_model=retry_model
                                            )
        else:
            # fallback: pass the raw content to parser (or retry)
            try:
                final_output = parser.parse(json.dumps(content))
            except Exception as e:
                print("\n\nException during parse (fallback list):")
                print(e)
                retry_model = get_default_retry_model()
                final_output = retry_output(output, parser, prompt,
                                            submission=student_submission,
                                            retry_model=retry_model
                                            )

    else:
        final_output = content
        if DEBUG:
            print("\n\nFinal Output (not a string, dict, nor list):")
            pprint(final_output)

    return final_output


def get_feedback_completion_chain(llm: BaseChatModel,
                                  pydantic_object: Type[T],
                                  feedback_type_list: list,
                                  assignment: str,
                                  solution: str,
                                  course_name: str
                                  ) -> tuple[
    RunnableSerializable, CustomPydanticOutputParser, PromptTemplate]:
    """Return the completion chain that can be used to get feedback on student submissions"""
    parser = CustomPydanticOutputParser(pydantic_object=pydantic_object,
                                        feedback_type_list=feedback_type_list
                                        )

    format_instructions = parser.get_format_instructions()

    prompt = PromptTemplate(
        # template_format="jinja2",
        input_variables=["submission"],
        partial_variables={
            "format_instructions": format_instructions,
            "feedback_types": "\n\t".join(feedback_type_list),
            "assignment": assignment,
            "solution": solution,
            "course_name": course_name,

        },
        template=(
            # ASSIGNMENT_FEEDBACK_PROMPT_BASE
            CODE_ASSIGNMENT_FEEDBACK_PROMPT_BASE
        ).strip(),
    )

    # prompt_value = prompt.format_prompt(exam_instructions=EXAM_INSTRUCTIONS, submission=STUDENT_SUBMISSION)
    # pprint(prompt_value)

    llm.bind(
        response_format={"type": "json_object"}
    )

    completion_chain = prompt | llm
    # completion_chain = LLMChain(prompt=prompt, llm=llm)

    return completion_chain, parser, prompt


async def get_feedback_from_completion_chain(
        student_submission: str,
        completion_chain: RunnableSerializable,
        parser: BaseOutputParser, prompt: PromptTemplate,
        callback: BaseCallbackHandler = None
):
    config = None
    if callback:
        config = {'callbacks': [callback]}

    output = await completion_chain.ainvoke({
        "submission": student_submission,
        "response_format": {"type": "json_object"}
    },
        config=config)

    # print("\n\nOutput:")
    # pprint(output)

    try:
        final_output = parser.parse(output.content)
        # final_output = parser.parse(output.get('text'))
    except Exception as e:
        print(e)
        # retry_model = get_llm_model_from_runnable_serializable(completion_chain)
        retry_model = get_default_retry_model()
        final_output = retry_output(output, parser, prompt,
                                    submission=student_submission,
                                    retry_model=retry_model
                                    )

    # print("\n\nFinal Output:")
    # pprint(output)

    return final_output


async def generate_feedback(llm: BaseChatModel, pydantic_object: Type[T], feedback_type_list: list, assignment: str,
                            solution: str, student_submission: str, course_name: str, is_code_assignment=True,
                            callback: BaseCallbackHandler = None) -> str:
    """
    Generates feedback based on the assignment, solution, student submission and course name using the LLM model.
    """
    parser = CustomPydanticOutputParser(pydantic_object=pydantic_object,
                                        feedback_type_list=feedback_type_list)

    format_instructions = parser.get_format_instructions()

    prompt_base = ASSIGNMENT_FEEDBACK_PROMPT_BASE
    if is_code_assignment:
        prompt_base = CODE_ASSIGNMENT_FEEDBACK_PROMPT_BASE

    prompt = PromptTemplate(
        # template_format="jinja2",
        input_variables=["assignment", "solution", "submission", "course_name"],
        partial_variables={
            "format_instructions": format_instructions,
            "feedback_types": "\n\t".join(feedback_type_list)

        },
        template=(
            prompt_base
        ).strip(),
    )

    # prompt_value = prompt.format_prompt(exam_instructions=EXAM_INSTRUCTIONS, submission=STUDENT_SUBMISSION)
    # pprint(prompt_value)

    llm.bind(
        response_format={"type": "json_object"}
    )

    completion_chain = prompt | llm
    # completion_chain = LLMChain(prompt=prompt, llm=llm)

    config = None
    if callback:
        config = {'callbacks': [callback]}

    output = await completion_chain.ainvoke({
        "assignment": assignment,
        "solution": solution,
        "submission": student_submission,
        "course_name": course_name,
        "response_format": {"type": "json_object"}

    }, config=config)

    # print("\n\nOutput:")
    # pprint(output.content)
    # pprint(output.get('text'))

    try:
        final_output = parser.parse(output.content)
        # final_output = parser.parse(output.get('text'))
    except Exception as e:
        print(e)
        # retry_model = get_llm_model_from_runnable_serializable(completion_chain)
        retry_model = get_default_retry_model()
        final_output = retry_output(output, parser, prompt,
                                    assignment=assignment,
                                    solution=solution,
                                    submission=student_submission,
                                    course_name=course_name,
                                    retry_model=retry_model
                                    )

    print("\n\nFinal Output:")
    pprint(output)

    return final_output


def generate_assignment_feedback_grade(llm: BaseChatModel, assignment: str,
                                       rubric_criteria_markdown_table: str, student_submission: str,
                                       student_file_name: str,
                                       total_possible_points: str) -> str:
    """
    Generates feedback and grade based on the assignment instructions, grading rubric, student submission and total possible points using the LLM model.
    """

    prompt = PromptTemplate(
        # template_format="jinja2",
        input_variables=["submission", "submission_file_name"],
        partial_variables={
            "assignment": assignment,
            "rubric_criteria_markdown_table": rubric_criteria_markdown_table,
            "total_possible_points": total_possible_points

        },
        template=(
            GRADE_ASSIGNMENT_WITH_FEEDBACK_PROMPT_BASE
        ).strip(),
    )

    prompt_value = prompt.format_prompt(submission=student_submission, submission_file_name=student_file_name)
    pprint(prompt_value)

    # llm.bind(
    #    response_format={"type": "json_object"}
    # )

    completion_chain = prompt | llm
    # completion_chain = LLMChain(llm=llm, prompt=prompt)

    output = completion_chain.invoke({
        "submission": student_submission,
        "submission_file_name": student_file_name,
        # "response_format": {"type": "json_object"}
    })

    return output.content
    # return output.get('text')
