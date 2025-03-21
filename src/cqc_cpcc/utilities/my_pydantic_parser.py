#  Copyright (c) 2025. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import json
from typing import List

from langchain_core.output_parsers import PydanticOutputParser

class CustomPydanticOutputParser(PydanticOutputParser):
    """Custom parser to modify the schema before getting format instructions."""

    major_error_type_list: List[str] = None
    minor_error_type_list: List[str] = None
    feedback_type_list: List[str] = None

    def get_format_instructions(self) -> str:
        # Copy schema to avoid altering original Pydantic schema.
        schema = {k: v for k, v in self.pydantic_object.schema().items()}

        # Remove extraneous fields.
        reduced_schema = schema
        if "title" in reduced_schema:
            del reduced_schema["title"]
        if "type" in reduced_schema:
            del reduced_schema["type"]

        # Modify the schema to filter errors
        if "properties" in reduced_schema:
            if "all_major_errors" in reduced_schema["properties"]:
                reduced_schema["properties"]["all_major_errors"]["items"]["enum"] = self.major_error_type_list
            if "all_minor_errors" in reduced_schema["properties"]:
                reduced_schema["properties"]["all_minor_errors"]["items"]["enum"] = self.minor_error_type_list
            if "all_feedback" in reduced_schema["properties"]:
                reduced_schema["properties"]["all_feedback"]["items"]["enum"] = self.feedback_type_list

        if "definitions" in reduced_schema:
            if "MajorErrorType" in reduced_schema["definitions"]:
                reduced_schema["definitions"]["MajorErrorType"]["enum"] = self.major_error_type_list
            if "MinorErrorType" in reduced_schema["definitions"]:
                reduced_schema["definitions"]["MinorErrorType"]["enum"] = self.minor_error_type_list
            if "FeedbackType" in reduced_schema["definitions"]:
                reduced_schema["definitions"]["FeedbackType"]["enum"] = self.feedback_type_list

        # Ensure json in context is well-formed with double quotes.
        schema_str = json.dumps(reduced_schema)

        return _PYDANTIC_FORMAT_INSTRUCTIONS.format(schema=schema_str)

_PYDANTIC_FORMAT_INSTRUCTIONS = """The output should be formatted as a JSON instance that conforms to the JSON schema below.

As an example, for the schema {{"properties": {{"foo": {{"title": "Foo", "description": "a list of strings", "type": "array", "items": {{"type": "string"}}}}}}, "required": ["foo"]}}
the object {{"foo": ["bar", "baz"]}} is a well-formatted instance of the schema. The object {{"properties": {{"foo": ["bar", "baz"]}}}} is not well-formatted.

Here is the output schema:
```
{schema}
```"""  # noqa: E501
